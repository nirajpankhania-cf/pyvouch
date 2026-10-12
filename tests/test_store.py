"""Run persistence."""

from __future__ import annotations

import time
from pathlib import Path

from vouch.core.report import AgentCost, AgentHunk, AgentSection, ReportMeta, build_report
from vouch.core.types import Finding, TaskInfo
from vouch.store.runs import RunStore, default_db_path

TASK = TaskInfo(text="add retry to the http client", source="flag")


def finding(severity: str = "warn", code: str = "placeholder-code") -> Finding:
    return Finding(
        check="placeholders",
        code=code,
        severity=severity,
        message="stub added",
        confidence="high",
        file="src/client.py",
        line=3,
    )


def report(findings=None, agent=None, branch="main"):
    return build_report(
        TASK,
        findings or [],
        agent,
        ReportMeta(branch=branch, commit="abc1234", repo_root="/repo"),
    )


class TestSaveAndGet:
    def test_round_trips_the_whole_report(self):
        store = RunStore(":memory:")
        run_id = store.save(report([finding()]))
        stored = store.get(run_id)
        assert stored["version"] == 1
        assert stored["task"]["text"] == TASK.text
        assert stored["deterministic"][0]["code"] == "placeholder-code"

    def test_ids_are_unique(self):
        store = RunStore(":memory:")
        assert store.save(report()) != store.save(report())

    def test_unknown_id_is_none(self):
        assert RunStore(":memory:").get("nope") is None

    def test_wire_format_is_preserved(self):
        store = RunStore(":memory:")
        agent = AgentSection(
            ran=True,
            hunks=[],
            findings=[],
            summary="ok",
            cost=AgentCost(input_tokens=7, output_tokens=2, tool_calls=1),
        )
        stored = store.get(store.save(report(agent=agent)))
        assert stored["agent"]["cost"]["inputTokens"] == 7


class TestListing:
    def test_empty_store_lists_nothing(self):
        assert RunStore(":memory:").list() == []

    def test_newest_first(self):
        store = RunStore(":memory:")
        store.save(report(branch="first"))
        time.sleep(0.002)
        store.save(report(branch="second"))
        assert [run.branch for run in store.list()] == ["second", "first"]

    def test_summary_carries_counts_not_the_report(self):
        store = RunStore(":memory:")
        store.save(report([finding("error"), finding("warn"), finding("info")]))
        (summary,) = store.list()
        assert summary.finding_count == 3
        assert summary.error_count == 1
        assert summary.verdict == "fail"
        assert not hasattr(summary, "deterministic")

    def test_agent_findings_count_toward_the_summary(self):
        store = RunStore(":memory:")
        agent = AgentSection(
            ran=True,
            hunks=[AgentHunk(file="a.py", range="1-2", classification="unrequested", reason="r")],
            findings=[finding("warn", "unrequested-change")],
            summary="s",
        )
        store.save(report(agent=agent))
        (summary,) = store.list()
        assert summary.finding_count == 1
        assert summary.agent_ran is True

    def test_limit_and_offset(self):
        store = RunStore(":memory:")
        for index in range(5):
            store.save(report(branch=f"b{index}"))
            time.sleep(0.002)
        assert len(store.list(limit=2)) == 2
        assert store.list(limit=2, offset=2)[0].branch == "b2"

    def test_count_tracks_saves(self):
        store = RunStore(":memory:")
        assert store.count() == 0
        store.save(report())
        store.save(report())
        assert store.count() == 2

    def test_duration_is_recorded(self):
        store = RunStore(":memory:")
        store.save(report(), duration_ms=42.5)
        assert store.list()[0].duration_ms == 42.5


class TestOnDisk:
    def test_file_is_created_and_survives_reopening(self, tmp_path: Path):
        db = tmp_path / "nested" / "runs.db"
        run_id = RunStore(db).save(report([finding()]))
        assert db.exists()
        # A fresh store object must see the same data.
        assert RunStore(db).get(run_id) is not None

    def test_schema_creation_is_idempotent(self, tmp_path: Path):
        db = tmp_path / "runs.db"
        RunStore(db).save(report())
        assert RunStore(db).count() == 1


class TestDefaultPath:
    def test_env_override_wins(self, monkeypatch, tmp_path: Path):
        target = tmp_path / "custom.db"
        monkeypatch.setenv("VOUCH_DB", str(target))
        assert default_db_path() == target

    def test_falls_back_to_a_data_directory(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("VOUCH_DB", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert default_db_path() == tmp_path / "vouch" / "runs.db"
