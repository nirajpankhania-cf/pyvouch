"""CLI surface. Exit codes are API — CI users depend on them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vouch import __version__
from vouch.cli import app
from vouch.core.types import ALL_ISSUE_CODES
from vouch.store.runs import RunStore

runner = CliRunner()


class TestMetaCommands:
    def test_version(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.stdout

    def test_list_codes_prints_every_code(self):
        result = runner.invoke(app, ["list-codes"])
        assert result.exit_code == 0
        for code in ALL_ISSUE_CODES:
            assert code in result.stdout

    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        assert "Usage" in result.stdout
        assert "check" in result.stdout

    def test_check_help_lists_the_documented_flags(self):
        result = runner.invoke(app, ["check", "--help"])
        for flag in ("--message", "--staged", "--base", "--json", "--no-agent", "--model"):
            assert flag in result.stdout


class TestExitCodes:
    def test_clean_repo_exits_zero(self, git_repo: Path, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = runner.invoke(app, ["check", "-m", "no changes expected", "--no-agent"])
        assert result.exit_code == 0

    def test_findings_exit_one(self, git_repo: Path, monkeypatch):
        (git_repo / "worker.py").write_text("def f():\n    pass\n", encoding="utf-8")
        monkeypatch.chdir(git_repo)
        result = runner.invoke(app, ["check", "-m", "add a worker", "--no-agent"])
        assert result.exit_code == 1

    def test_not_a_repo_exits_two(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["check", "-m", "anything", "--no-agent"])
        assert result.exit_code == 2
        assert "not a git repository" in result.stderr

    def test_bad_base_ref_exits_two(self, git_repo: Path, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = runner.invoke(
            app, ["check", "-m", "anything", "--no-agent", "--base", "nope"]
        )
        assert result.exit_code == 2

    def test_mutually_exclusive_modes_exit_two(self, git_repo: Path, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = runner.invoke(
            app, ["check", "-m", "x", "--no-agent", "--staged", "--base", "main"]
        )
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr

    def test_missing_task_exits_two(self, git_repo: Path, monkeypatch):
        monkeypatch.chdir(git_repo)
        # --json implies non-interactive, so no prompt can rescue a missing task.
        result = runner.invoke(app, ["check", "--no-agent", "--json"])
        assert result.exit_code == 2


class TestJsonOutput:
    def test_json_is_parseable_and_matches_the_schema(self, git_repo: Path, monkeypatch):
        (git_repo / "worker.py").write_text("def f():\n    pass\n", encoding="utf-8")
        monkeypatch.chdir(git_repo)
        result = runner.invoke(app, ["check", "-m", "add a worker", "--no-agent", "--json"])
        payload = json.loads(result.stdout)
        assert payload["version"] == 1
        assert set(payload) == {
            "version",
            "task",
            "changes",
            "deterministic",
            "agent",
            "verdict",
            "meta",
        }
        assert payload["agent"] == {"ran": False}

    def test_changes_carry_the_diff_the_findings_refer_to(self, git_repo: Path, monkeypatch):
        (git_repo / "worker.py").write_text("def f():\n    pass\n", encoding="utf-8")
        monkeypatch.chdir(git_repo)
        result = runner.invoke(app, ["check", "-m", "add a worker", "--no-agent", "--json"])
        (change,) = json.loads(result.stdout)["changes"]
        assert change["file"] == "worker.py"
        assert change["status"] == "added"
        assert [line["text"] for line in change["lines"]] == ["def f():", "    pass"]

    def test_json_mode_prints_no_human_decoration(self, git_repo: Path, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = runner.invoke(app, ["check", "-m", "nothing", "--no-agent", "--json"])
        json.loads(result.stdout)  # the whole of stdout must be the report


class TestRecordingRuns:
    def test_save_writes_a_run_the_ui_can_open(self, git_repo: Path, tmp_path, monkeypatch):
        db = tmp_path / "cli-runs.db"
        monkeypatch.setenv("VOUCH_DB", str(db))
        monkeypatch.chdir(git_repo)
        runner.invoke(app, ["check", "-m", "add a worker", "--no-agent", "--save"])
        store = RunStore(db)
        (summary,) = store.list()
        assert summary.task_text == "add a worker"
        assert store.get(summary.id)["version"] == 1

    def test_configured_store_records_without_the_flag(self, git_repo: Path, tmp_path, monkeypatch):
        db = tmp_path / "configured.db"
        monkeypatch.setenv("VOUCH_DB", str(db))
        monkeypatch.chdir(git_repo)
        runner.invoke(app, ["check", "-m", "nothing", "--no-agent"])
        assert RunStore(db).count() == 1

    def test_nothing_is_recorded_by_default(self, git_repo: Path, tmp_path, monkeypatch):
        db = tmp_path / "unused.db"
        monkeypatch.delenv("VOUCH_DB", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.chdir(git_repo)
        runner.invoke(app, ["check", "-m", "nothing", "--no-agent"])
        assert not db.exists()
        assert not (tmp_path / "vouch").exists()

    def test_saving_does_not_disturb_json_output(self, git_repo: Path, tmp_path, monkeypatch):
        monkeypatch.setenv("VOUCH_DB", str(tmp_path / "runs.db"))
        monkeypatch.chdir(git_repo)
        result = runner.invoke(app, ["check", "-m", "nothing", "--no-agent", "--json"])
        json.loads(result.stdout)

    def test_a_broken_store_does_not_change_the_exit_code(
        self, git_repo: Path, tmp_path, monkeypatch
    ):
        # A directory where the database file should be: sqlite cannot open it.
        broken = tmp_path / "runs.db"
        broken.mkdir()
        monkeypatch.setenv("VOUCH_DB", str(broken))
        monkeypatch.chdir(git_repo)
        result = runner.invoke(app, ["check", "-m", "nothing", "--no-agent"])
        assert result.exit_code == 0


class TestInstalledEntryPoint:
    def test_console_script_is_executable(self):
        # The packaged entry point, not just the in-process app. Resolved
        # next to the running interpreter so the venv's script is used.
        script = Path(sys.executable).parent / "vouch"
        if not script.exists():
            pytest.skip("console script not installed in this environment")
        result = subprocess.run([str(script), "version"], capture_output=True, text=True)
        assert result.returncode == 0
        assert __version__ in result.stdout
