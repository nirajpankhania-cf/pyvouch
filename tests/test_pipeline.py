"""End-to-end pipeline runs against real git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vouch.checks.registry import ALL_CHECKS, CHECK_NAMES, Check
from vouch.context.diff import DiffMode
from vouch.context.task import TaskError
from vouch.core.pipeline import CheckCrashError, PipelineOptions, run_pipeline
from vouch.core.types import ALL_ISSUE_CODES, CODE_MEANINGS, TaskInfo


def run(repo: Path, task: str = "do the thing", **kwargs):
    options = PipelineOptions(
        mode=kwargs.pop("mode", DiffMode.working()),
        task=TaskInfo(text=task, source="flag"),
        cwd=str(repo),
        agent=kwargs.pop("agent", False),
        prompt=False,
        **kwargs,
    )
    return run_pipeline(options)


class TestCleanRuns:
    def test_no_changes_is_clean_and_exits_zero(self, git_repo: Path):
        result = run(git_repo)
        assert result.report.verdict == "clean"
        assert result.report.deterministic == []

    def test_relevant_change_is_clean(self, git_repo: Path):
        (git_repo / "app.py").write_text(
            "def greet(name):\n    return f'hi {name}'\n", encoding="utf-8"
        )
        result = run(git_repo, task="make greet take a name argument")
        assert result.report.verdict == "clean"

    def test_report_carries_git_metadata(self, git_repo: Path):
        result = run(git_repo)
        assert result.report.meta.branch == "main"
        assert result.report.meta.commit
        assert result.report.meta.repo_root

    def test_duration_is_recorded(self, git_repo: Path):
        assert run(git_repo).duration_ms >= 0


class TestFindingsEndToEnd:
    def test_stub_in_a_new_file_is_caught(self, git_repo: Path):
        (git_repo / "worker.py").write_text(
            "def process():\n    pass\n", encoding="utf-8"
        )
        result = run(git_repo, task="add a worker that processes the queue")
        codes = {f.code for f in result.report.deterministic}
        assert "placeholder-code" in codes
        assert result.report.verdict == "review"

    def test_deleted_test_file_fails(self, git_repo: Path):
        tests_dir = git_repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("def test_greet():\n    assert True\n", "utf-8")
        subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "add tests"], cwd=git_repo, check=True, capture_output=True
        )
        (tests_dir / "test_app.py").unlink()

        result = run(git_repo, task="speed up the greet function")
        assert result.report.verdict == "fail"
        assert any(f.code == "test-tampering" for f in result.report.deterministic)

    def test_hallucinated_internal_import_is_caught(self, git_repo: Path):
        (git_repo / "app.py").write_text(
            "from helpers import missing_thing\n\n\ndef greet():\n    return 'hi'\n",
            encoding="utf-8",
        )
        (git_repo / "helpers.py").write_text("def real_thing():\n    return 1\n", encoding="utf-8")
        result = run(git_repo, task="make greet use the helper")
        assert any(f.code == "unresolved-import" for f in result.report.deterministic)

    def test_unrelated_file_drifts_scope(self, git_repo: Path):
        (git_repo / "billing.py").write_text("TAX = 0.2\n", encoding="utf-8")
        result = run(git_repo, task="rename the greet function to welcome")
        assert any(f.code == "scope-drift" for f in result.report.deterministic)

    def test_findings_are_ordered_most_serious_first(self, git_repo: Path):
        tests_dir = git_repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("def test_x():\n    assert True\n", "utf-8")
        subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "tests"], cwd=git_repo, check=True, capture_output=True
        )
        (tests_dir / "test_app.py").unlink()
        (git_repo / "worker.py").write_text("def f():\n    pass  # TODO\n", encoding="utf-8")

        severities = [f.severity for f in run(git_repo, task="tidy up").report.deterministic]
        assert severities == sorted(severities, key=["error", "warn", "info"].index)


class TestAgentStatus:
    def test_no_agent_reports_disabled(self, git_repo: Path):
        assert run(git_repo).agent_status == "disabled"

    def test_empty_diff_reports_no_hunks(self, git_repo: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        result = run(git_repo, agent=True)
        assert result.agent_status == "no-hunks"

    def test_missing_api_key_degrades_cleanly(self, git_repo: Path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        (git_repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        result = run(git_repo, agent=True)
        assert result.agent_status == "no-api-key"
        assert result.report.agent.ran is False
        # Pass 1 still produced a usable report.
        assert result.report.verdict in {"clean", "review", "fail"}

    def test_agent_section_omits_fields_when_not_run(self, git_repo: Path):
        wire = run(git_repo).report.to_json_dict()
        assert wire["agent"] == {"ran": False}


class TestCheckIsolation:
    def test_a_crashing_check_is_a_tool_error_not_a_finding(self, git_repo: Path, monkeypatch):
        def explode(ctx):
            raise RuntimeError("boom")

        # The registry binds check functions at import, so replace the entry
        # rather than the module attribute.
        monkeypatch.setattr(
            "vouch.core.pipeline.ALL_CHECKS", (Check("scope", explode),), raising=True
        )
        (git_repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(CheckCrashError, match="'scope' check crashed"):
            run(git_repo)

    def test_checks_can_be_selectively_enabled(self, git_repo: Path):
        (git_repo / "billing.py").write_text("TAX = 0.2\n", encoding="utf-8")
        result = run(git_repo, task="rename greet to welcome", enabled_checks=("placeholders",))
        assert not any(f.code == "scope-drift" for f in result.report.deterministic)


class TestDiffModes:
    def test_staged_mode_ignores_the_working_tree(self, git_repo: Path):
        (git_repo / "staged.py").write_text("s = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "staged.py"], cwd=git_repo, check=True, capture_output=True)
        (git_repo / "loose.py").write_text("l = 1\n", encoding="utf-8")
        result = run(git_repo, task="add the staged module", mode=DiffMode.staged())
        files = {f.file for f in result.report.deterministic}
        assert "loose.py" not in files


class TestTaskResolution:
    def test_missing_task_raises(self, git_repo: Path):
        options = PipelineOptions(
            mode=DiffMode.working(), cwd=str(git_repo), agent=False, prompt=False
        )
        with pytest.raises(TaskError):
            run_pipeline(options)

    def test_task_file_is_picked_up(self, git_repo: Path):
        (git_repo / "TASK.md").write_text("add a welcome banner\n", encoding="utf-8")
        options = PipelineOptions(
            mode=DiffMode.working(), cwd=str(git_repo), agent=False, prompt=False
        )
        result = run_pipeline(options)
        assert result.report.task.source == "taskfile"
        assert result.report.task.text == "add a welcome banner"


class TestRegistryContract:
    def test_every_check_is_registered_once(self):
        assert len(CHECK_NAMES) == len(set(CHECK_NAMES))

    def test_registry_covers_the_four_documented_checks(self):
        assert set(CHECK_NAMES) == {"placeholders", "tests", "imports", "scope"}

    def test_every_issue_code_has_a_meaning(self):
        assert set(CODE_MEANINGS) == set(ALL_ISSUE_CODES)

    def test_checks_are_callable(self):
        for check in ALL_CHECKS:
            assert callable(check.run)
