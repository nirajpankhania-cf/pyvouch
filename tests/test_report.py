"""Verdict derivation and wire-format contracts (docs/SPEC.md)."""

from __future__ import annotations

import time

import pytest

from vouch.core.report import (
    AgentCost,
    AgentHunk,
    AgentSection,
    agent_findings_of,
    build_report,
    exit_code_for,
    verdict_of,
)
from vouch.core.types import Finding, TaskInfo

TASK = TaskInfo(text="add retry to the http client", source="flag")


def finding(severity: str = "warn", code: str = "placeholder-code") -> Finding:
    return Finding(
        check="placeholders",
        code=code,
        severity=severity,
        message="stub added",
        confidence="high",
        file="src/client.py",
        line=12,
    )


def hunk(classification: str = "requested", file: str = "src/client.py") -> AgentHunk:
    return AgentHunk(file=file, range="12-20", classification=classification, reason="because")


class TestVerdict:
    def test_no_findings_is_clean(self):
        assert verdict_of([]) == "clean"

    def test_error_severity_fails(self):
        assert verdict_of([finding("error")]) == "fail"

    def test_warn_is_review(self):
        assert verdict_of([finding("warn")]) == "review"

    def test_info_only_is_still_review(self):
        # Deliberate: scope drift alone is consequential and exits 1.
        assert verdict_of([finding("info", "scope-drift")]) == "review"

    def test_error_wins_over_warn(self):
        assert verdict_of([finding("warn"), finding("error")]) == "fail"

    def test_agent_not_run_cannot_flag(self):
        section = AgentSection(ran=False)
        assert verdict_of([], section) == "clean"

    def test_unrequested_hunk_alone_is_review(self):
        section = AgentSection(ran=True, hunks=[hunk("unrequested")], findings=[])
        assert verdict_of([], section) == "review"

    def test_requested_hunks_only_stay_clean(self):
        section = AgentSection(ran=True, hunks=[hunk("requested"), hunk("supporting")], findings=[])
        assert verdict_of([], section) == "clean"

    def test_agent_finding_alone_is_review(self):
        section = AgentSection(ran=True, hunks=[], findings=[finding("warn", "docs-drift")])
        assert verdict_of([], section) == "review"

    def test_agent_cannot_escalate_to_fail(self):
        section = AgentSection(ran=True, hunks=[hunk("unrequested")], findings=[])
        assert verdict_of([], section) != "fail"


@pytest.mark.parametrize(
    ("verdict", "code"),
    [("clean", 0), ("review", 1), ("fail", 1)],
)
def test_exit_codes(verdict, code):
    assert exit_code_for(verdict) == code


class TestDerivedFindings:
    def test_one_derived_finding_per_unrequested_hunk(self):
        hunks = [hunk("unrequested"), hunk("requested"), hunk("unrequested", "src/other.py")]
        out = agent_findings_of(hunks, [])
        assert [f.code for f in out] == ["unrequested-change"] * 2
        assert {f.file for f in out} == {"src/client.py", "src/other.py"}

    def test_derived_finding_takes_line_from_range(self):
        out = agent_findings_of([hunk("unrequested")], [])
        assert out[0].line == 12
        assert out[0].confidence == "medium"

    def test_model_emitted_unrequested_change_is_dropped(self):
        # Derivation is the single source of this code — no double-reporting.
        out = agent_findings_of([], [finding("warn", "unrequested-change")])
        assert out == []

    def test_model_findings_are_forced_to_warn(self):
        out = agent_findings_of([], [finding("error", "dead-integration")])
        assert out[0].severity == "warn"
        assert out[0].check == "agent"


class TestWireFormat:
    def test_cost_keys_are_camel_case(self):
        section = AgentSection(
            ran=True,
            hunks=[],
            findings=[],
            summary="ok",
            cost=AgentCost(input_tokens=10, output_tokens=2, tool_calls=3),
        )
        wire = build_report(TASK, [], section).to_json_dict()
        assert wire["agent"]["cost"] == {"inputTokens": 10, "outputTokens": 2, "toolCalls": 3}

    def test_meta_and_version_keys(self):
        wire = build_report(TASK, []).to_json_dict()
        assert wire["version"] == 1
        assert "createdAt" in wire["meta"]
        assert wire["task"] == {"text": TASK.text, "source": "flag"}

    def test_absent_agent_section_carries_only_ran(self):
        wire = build_report(TASK, []).to_json_dict()
        assert wire["agent"] == {"ran": False}

    def test_finding_omits_empty_location(self):
        bare = Finding(
            check="agent",
            code="request-unfulfilled",
            severity="warn",
            message="not delivered",
            confidence="medium",
        )
        wire = build_report(TASK, [bare]).to_json_dict()
        assert "file" not in wire["deterministic"][0]
        assert "line" not in wire["deterministic"][0]

    def test_verdict_is_in_the_report(self):
        wire = build_report(TASK, [finding("error")]).to_json_dict()
        assert wire["verdict"] == "fail"

    def test_created_at_is_per_report_not_per_import(self):
        # A bare ReportMeta() default would stamp every report with the
        # module's import time instead of its own creation time.
        first = build_report(TASK, []).meta.created_at
        time.sleep(0.002)
        second = build_report(TASK, []).meta.created_at
        assert first != second
