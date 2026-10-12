"""Terminal rendering. The JSON report is the wire format; this is for humans."""

from __future__ import annotations

import typer

from vouch.core.pipeline import PipelineResult
from vouch.core.types import AgentStatus, Finding

_SEVERITY_COLOR = {
    "error": typer.colors.RED,
    "warn": typer.colors.YELLOW,
    "info": typer.colors.BLUE,
}

_VERDICT_STYLE = {
    "clean": (typer.colors.GREEN, "clean"),
    "review": (typer.colors.YELLOW, "needs review"),
    "fail": (typer.colors.RED, "fail"),
}

_AGENT_NOTICE: dict[AgentStatus, str] = {
    "disabled": "Pass 2 skipped (--no-agent).",
    "no-api-key": "Pass 2 skipped: set ANTHROPIC_API_KEY to classify hunks against the task.",
    "no-hunks": "Pass 2 skipped: nothing changed.",
}

_TASK_SOURCE_LABEL = {
    "flag": "-m",
    "taskfile": "TASK.md",
    "transcript": "transcript (guessed)",
    "prompt": "prompt",
    "api": "api",
}


def render(result: PipelineResult) -> None:
    report = result.report
    typer.echo()
    _render_task(report)

    if not report.deterministic and not (report.agent.findings or []):
        typer.secho("No findings.", fg=typer.colors.GREEN)
    else:
        _render_findings("Pass 1 — static", report.deterministic)
        _render_findings("Pass 2 — intent", report.agent.findings or [])

    _render_hunks(report)
    _render_agent_notice(result)
    _render_verdict(result)


def _render_task(report) -> None:
    source = _TASK_SOURCE_LABEL.get(report.task.source, report.task.source)
    first_line = report.task.text.strip().splitlines()[0]
    typer.secho(f"Task ({source}): ", fg=typer.colors.CYAN, nl=False)
    typer.echo(_truncate(first_line, 90))
    if report.task.source == "transcript":
        # Never silently judge against a guessed task.
        typer.secho(
            "  ^ guessed from your transcript — pass -m if that's not the task.",
            fg=typer.colors.YELLOW,
        )
    typer.echo()


def _render_findings(heading: str, findings: list[Finding]) -> None:
    if not findings:
        return
    typer.secho(heading, bold=True)
    for finding in findings:
        colour = _SEVERITY_COLOR.get(finding.severity, typer.colors.WHITE)
        typer.secho(f"  {finding.severity:<5}", fg=colour, nl=False)
        typer.secho(f" {finding.code:<26}", fg=typer.colors.MAGENTA, nl=False)
        typer.echo(f" {_location(finding)}")
        typer.echo(f"        {finding.message}")
        if finding.confidence == "low":
            typer.secho("        (low confidence — heuristic)", fg=typer.colors.BRIGHT_BLACK)
    typer.echo()


def _location(finding: Finding) -> str:
    if not finding.file:
        return "(no location)"
    return f"{finding.file}:{finding.line}" if finding.line else finding.file


def _render_hunks(report) -> None:
    hunks = report.agent.hunks or []
    unrequested = [h for h in hunks if h.classification == "unrequested"]
    if not unrequested:
        return
    typer.secho("Unrequested hunks", bold=True)
    for hunk in unrequested:
        typer.echo(f"  {hunk.file}:{hunk.range} — {hunk.reason}")
    typer.echo()


def _render_agent_notice(result: PipelineResult) -> None:
    if result.agent_status == "ran":
        cost = result.report.agent.cost
        if cost:
            typer.secho(
                f"Pass 2 ran: {cost.tool_calls} tool calls, "
                f"{cost.input_tokens + cost.output_tokens} tokens.",
                fg=typer.colors.BRIGHT_BLACK,
            )
        return
    if result.agent_status == "error":
        typer.secho(
            f"Pass 2 failed, reporting Pass 1 only: {result.agent_error}",
            fg=typer.colors.YELLOW,
        )
        return
    notice = _AGENT_NOTICE.get(result.agent_status)
    if notice:
        typer.secho(notice, fg=typer.colors.BRIGHT_BLACK)


def _render_verdict(result: PipelineResult) -> None:
    colour, label = _VERDICT_STYLE[result.report.verdict]
    counts = _counts(result.report)
    typer.secho(f"\n{label.upper()}", fg=colour, bold=True, nl=False)
    typer.echo(f"  {counts}  ({result.duration_ms:.0f}ms)")


def _counts(report) -> str:
    findings = list(report.deterministic) + list(report.agent.findings or [])
    if not findings:
        return "no findings"
    by_severity: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
    return ", ".join(f"{count} {name}" for name, count in by_severity.items())


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
