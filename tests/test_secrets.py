"""Credentials must not reach the model. Pass 2 is the only hop off the machine."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vouch.agent.prompts import _render_hunk, build_initial_prompt
from vouch.agent.secrets import holds_secrets
from vouch.agent.tools import execute_tool
from vouch.core.types import Hunk, HunkLine, TaskInfo

SECRET = "sk-ant-api03-REALLOOKINGSECRET"


def hunk_of(file: str, text: str = SECRET) -> Hunk:
    return Hunk(
        file=file,
        status="added",
        old_start=0,
        old_lines=0,
        new_start=1,
        new_lines=1,
        lines=[HunkLine(kind="add", text=text, new_line=1)],
    )


class TestClassification:
    @pytest.mark.parametrize(
        "path",
        [
            ".env",
            ".env.local",
            ".env.production",
            "config/.env",
            "deep/nested/dir/.env",
            ".netrc",
            ".npmrc",
            ".pgpass",
            "credentials",
            "~/.aws/credentials",
            "id_rsa",
            "id_rsa.bak",
            "id_ed25519",
            "secrets.json",
            "service-account.json",
            "server.pem",
            "private.key",
            "cert.p12",
            "bundle.pfx",
            "app.keystore",
            "debug.jks",
            "SERVER.PEM",
            ".ENV",
        ],
    )
    def test_withheld(self, path: str):
        assert holds_secrets(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".env.example",
            ".env.sample",
            "src/vouch/cli.py",
            "environment.py",
            "keyboard.py",
            "monkey.ts",
            "README.md",
            "docs/credentials-guide.md",
            "tests/test_env.py",
            "package.json",
        ],
    )
    def test_allowed(self, path: str):
        assert holds_secrets(path) is False

    def test_the_shipped_example_is_readable(self):
        # Withholding documentation would teach the model nothing.
        assert holds_secrets(".env.example") is False


class TestPromptRendering:
    def test_a_secret_hunk_body_is_withheld(self):
        rendered = _render_hunk(hunk_of(".env"))
        assert SECRET not in rendered
        assert "contents withheld" in rendered

    def test_the_path_is_still_shown_so_it_can_be_classified(self):
        rendered = _render_hunk(hunk_of(".env"))
        assert ".env" in rendered
        assert "1 line(s) added" in rendered

    def test_ordinary_code_is_unaffected(self):
        rendered = _render_hunk(hunk_of("app.py", "x = 1"))
        assert "x = 1" in rendered
        assert "withheld" not in rendered

    def test_the_whole_prompt_is_clean(self):
        prompt = build_initial_prompt(
            TaskInfo(text="add retries", source="flag"),
            [hunk_of(".env"), hunk_of("client.py", "retries = 3")],
        )
        assert SECRET not in prompt
        assert "retries = 3" in prompt


@pytest.fixture
def repo_with_secret(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={SECRET}\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("key = 'placeholder'\n", encoding="utf-8")
    git("add", "-A", "-f")
    git("commit", "-qm", "initial")
    return tmp_path


class TestTools:
    def test_read_file_refuses(self, repo_with_secret: Path):
        out = execute_tool("read_file", {"path": ".env"}, repo_with_secret)
        assert SECRET not in out
        assert "refusing to read a credentials file" in out

    def test_read_file_still_reads_code(self, repo_with_secret: Path):
        out = execute_tool("read_file", {"path": "app.py"}, repo_with_secret)
        assert "placeholder" in out

    def test_search_cannot_be_used_to_read_one(self, repo_with_secret: Path):
        out = execute_tool("search", {"pattern": "sk-ant"}, repo_with_secret)
        assert SECRET not in out

    def test_search_still_finds_code_matches(self, repo_with_secret: Path):
        out = execute_tool("search", {"pattern": "placeholder"}, repo_with_secret)
        assert "app.py" in out

    def test_a_search_matching_only_secrets_reports_nothing(self, repo_with_secret: Path):
        out = execute_tool("search", {"pattern": "ANTHROPIC_API_KEY"}, repo_with_secret)
        assert SECRET not in out
        assert "no matches" in out

    def test_list_dir_may_still_name_the_file(self, repo_with_secret: Path):
        # The name is the point: Pass 2 should be able to say "you added .env".
        out = execute_tool("list_dir", {"path": "."}, repo_with_secret)
        assert ".env" in out
        assert SECRET not in out
