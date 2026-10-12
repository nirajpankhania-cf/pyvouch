"""Orchestrates one vouch run: context → Pass 1 → Pass 2 → report.

No user-facing output here; the CLI and the API both render the result.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vouch.checks.registry import ALL_CHECKS
from vouch.context.diff import DiffMode, get_hunks
from vouch.context.gitcmd import current_branch, current_commit, is_inside_repo, repo_root
from vouch.context.project import build_project_access
from vouch.context.task import resolve_task
from vouch.core.report import AgentSection, ReportMeta, VouchReport, build_report
from vouch.core.types import AgentStatus, CheckContext, Finding, TaskInfo


class CheckCrashError(Exception):
    """A check raised. That's a vouch bug, never a finding — the CLI exits 2."""

    def __init__(self, check_name: str, cause: BaseException) -> None:
        super().__init__(
            f"internal error: the '{check_name}' check crashed ({cause}). "
            "This is a vouch bug — please report it."
        )
        self.check_name = check_name


@dataclass
class PipelineOptions:
    mode: DiffMode
    message: str | None = None
    cwd: str = "."
    # False is --no-agent.
    agent: bool = True
    model: str | None = None
    # Pre-resolved task, used by the API where there is no TTY or TASK.md.
    task: TaskInfo | None = None
    prompt: bool = True
    # False skips reading the task from an agent transcript.
    use_transcript: bool = True
    enabled_checks: tuple[str, ...] | None = None
    # Injected Anthropic client; tests script a fake instead of calling out.
    agent_client: Any = None


@dataclass
class PipelineResult:
    report: VouchReport
    duration_ms: float
    agent_status: AgentStatus
    agent_error: str | None = None


def run_pipeline(opts: PipelineOptions) -> PipelineResult:
    started = time.perf_counter()
    cwd = opts.cwd or "."
    root = repo_root(cwd) if is_inside_repo(cwd) else str(Path(cwd).resolve())

    task = opts.task or resolve_task(
        root, opts.message, prompt=opts.prompt, use_transcript=opts.use_transcript
    )
    hunks = get_hunks(opts.mode, root)
    context = CheckContext(
        hunks=hunks,
        task=task,
        project=build_project_access(hunks, root),
    )

    findings: list[Finding] = []
    for check in ALL_CHECKS:
        if opts.enabled_checks is not None and check.name not in opts.enabled_checks:
            continue
        try:
            findings.extend(check.run(context))
        except Exception as cause:  # a check bug must not look like a finding
            raise CheckCrashError(check.name, cause) from cause

    section, status, error = _maybe_run_agent(opts, context)
    meta = ReportMeta(
        commit=current_commit(root),
        branch=current_branch(root),
        repo_root=root,
    )

    return PipelineResult(
        report=build_report(task, _ordered(findings), section, meta, changes=hunks),
        duration_ms=(time.perf_counter() - started) * 1000,
        agent_status=status,
        agent_error=error,
    )


_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def _ordered(findings: list[Finding]) -> list[Finding]:
    """Most serious first, then by file and line, so output reads predictably."""
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), f.file or "", f.line or 0),
    )


def _maybe_run_agent(
    opts: PipelineOptions,
    context: CheckContext,
) -> tuple[AgentSection, AgentStatus, str | None]:
    """Pass 2, or an honest reason it didn't run.

    Agent failures degrade to a Pass-1-only report; they never crash a run.
    """
    if not opts.agent:
        return AgentSection(ran=False), "disabled", None
    if not context.hunks:
        return AgentSection(ran=False), "no-hunks", None
    if opts.agent_client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return AgentSection(ran=False), "no-api-key", None

    try:
        from vouch.agent.loop import run_agent

        section = run_agent(
            context,
            context.project.repo_root,
            model=opts.model,
            client=opts.agent_client,
        )
    except Exception as cause:
        return AgentSection(ran=False), "error", str(cause)
    return section, "ran", None
