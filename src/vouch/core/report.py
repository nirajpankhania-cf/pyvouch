"""The JSON report. This schema is API (``docs/SPEC.md``).

Additive changes (new fields) keep ``version``; breaking changes
(rename/remove/retype) bump it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from vouch.core.types import (
    Classification,
    Finding,
    TaskInfo,
    Verdict,
    WireModel,
)


class AgentHunk(WireModel):
    file: str
    range: str
    classification: Classification
    reason: str


class AgentCost(WireModel):
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0


class AgentSection(WireModel):
    """``ran: false`` carries no other fields; they are omitted on dump."""

    ran: bool = False
    hunks: list[AgentHunk] | None = None
    findings: list[Finding] | None = None
    summary: str | None = None
    cost: AgentCost | None = None


class ReportMeta(WireModel):
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    commit: str | None = None
    branch: str | None = None
    repo_root: str | None = None


class VouchReport(WireModel):
    version: int = 1
    task: TaskInfo
    deterministic: list[Finding] = Field(default_factory=list)
    agent: AgentSection = Field(default_factory=AgentSection)
    verdict: Verdict = "clean"
    # default_factory, not ReportMeta(): a bare instance would freeze
    # created_at at import time and stamp every report with it.
    meta: ReportMeta = Field(default_factory=ReportMeta)

    def to_json_dict(self) -> dict:
        """Wire form: camelCase keys, absent optionals dropped."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def verdict_of(findings: list[Finding], agent: AgentSection | None = None) -> Verdict:
    """Any deterministic error means fail.

    Otherwise any deterministic finding at all — including ``info`` — or any
    agent flag means review. The agent layer is consequential by design: scope
    creep alone produces a non-clean verdict. Unrequested hunks are checked
    directly as well as via derived findings, so per-code config filtering
    cannot silently downgrade the verdict.
    """
    if any(f.severity == "error" for f in findings):
        return "fail"
    agent_flagged = bool(
        agent
        and agent.ran
        and (
            (agent.findings or [])
            or any(h.classification == "unrequested" for h in (agent.hunks or []))
        )
    )
    if findings or agent_flagged:
        return "review"
    return "clean"


def exit_code_for(verdict: Verdict) -> int:
    """``0`` clean · ``1`` findings · ``2`` tool error (raised elsewhere)."""
    return 0 if verdict == "clean" else 1


def agent_findings_of(
    hunks: list[AgentHunk],
    model_findings: list[Finding],
) -> list[Finding]:
    """Materialize the agent verdict as issue-coded findings.

    The model's own findings are always ``warn`` — LLM judgment is never an
    objective error. One ``unrequested-change`` is derived per unrequested
    hunk; the model is told not to emit that code, and any it emits anyway is
    dropped here so derivation stays its single source.
    """
    reported = [
        f.model_copy(update={"check": "agent", "severity": "warn"})
        for f in model_findings
        if f.code != "unrequested-change"
    ]
    derived = [
        Finding(
            check="agent",
            code="unrequested-change",
            severity="warn",
            file=h.file,
            line=_range_start(h.range),
            message=h.reason,
            confidence="medium",
        )
        for h in hunks
        if h.classification == "unrequested"
    ]
    return reported + derived


def _range_start(range_text: str) -> int | None:
    head = range_text.split("-", 1)[0].strip()
    return int(head) if head.isdigit() else None


def build_report(
    task: TaskInfo,
    findings: list[Finding],
    agent: AgentSection | None = None,
    meta: ReportMeta | None = None,
) -> VouchReport:
    section = agent or AgentSection()
    return VouchReport(
        task=task,
        deterministic=findings,
        agent=section,
        verdict=verdict_of(findings, section),
        meta=meta or ReportMeta(),
    )


__all__ = [
    "AgentCost",
    "AgentHunk",
    "AgentSection",
    "ReportMeta",
    "VouchReport",
    "agent_findings_of",
    "build_report",
    "exit_code_for",
    "verdict_of",
]
