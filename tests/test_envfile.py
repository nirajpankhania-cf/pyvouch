"""`.env` loading. A real environment variable must always win."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vouch.envfile import load_env_file


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("VOUCH_ENV_FILE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("EXAMPLE_KEY", raising=False)


def write(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".env").write_text(body, encoding="utf-8")
    return directory


class TestLoading:
    def test_sets_a_value(self, tmp_path: Path):
        write(tmp_path, "ANTHROPIC_API_KEY=sk-test\n")
        assert load_env_file(tmp_path) == {"ANTHROPIC_API_KEY": "sk-test"}
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"

    def test_missing_file_is_not_an_error(self, tmp_path: Path):
        assert load_env_file(tmp_path) == {}

    def test_a_directory_named_env_is_ignored(self, tmp_path: Path):
        (tmp_path / ".env").mkdir()
        assert load_env_file(tmp_path) == {}

    def test_explicit_path_overrides_the_default(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "secrets.env"
        target.write_text("EXAMPLE_KEY=from-override\n", encoding="utf-8")
        write(tmp_path, "EXAMPLE_KEY=from-default\n")
        monkeypatch.setenv("VOUCH_ENV_FILE", str(target))
        assert load_env_file(tmp_path)["EXAMPLE_KEY"] == "from-override"


class TestPrecedence:
    def test_real_environment_wins(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
        write(tmp_path, "ANTHROPIC_API_KEY=from-file\n")
        assert load_env_file(tmp_path) == {}
        assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"

    def test_an_empty_shell_value_still_wins(self, tmp_path: Path, monkeypatch):
        # Exported-but-empty is a deliberate choice; honour it.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        write(tmp_path, "ANTHROPIC_API_KEY=from-file\n")
        assert load_env_file(tmp_path) == {}
        assert os.environ["ANTHROPIC_API_KEY"] == ""


class TestParsing:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("EXAMPLE_KEY=plain", "plain"),
            ('EXAMPLE_KEY="double quoted"', "double quoted"),
            ("EXAMPLE_KEY='single quoted'", "single quoted"),
            ("  EXAMPLE_KEY = spaced  ", "spaced"),
            ("export EXAMPLE_KEY=exported", "exported"),
            ("EXAMPLE_KEY=value # trailing note", "value"),
            ('EXAMPLE_KEY="value # kept"', "value # kept"),
            ("EXAMPLE_KEY=", ""),
            ("EXAMPLE_KEY=a=b=c", "a=b=c"),
        ],
    )
    def test_line_forms(self, tmp_path: Path, line: str, expected: str):
        write(tmp_path, line + "\n")
        assert load_env_file(tmp_path)["EXAMPLE_KEY"] == expected

    def test_comments_and_blanks_are_skipped(self, tmp_path: Path):
        write(tmp_path, "# a comment\n\n   \nEXAMPLE_KEY=kept\n")
        assert load_env_file(tmp_path) == {"EXAMPLE_KEY": "kept"}

    def test_lines_without_an_equals_are_skipped(self, tmp_path: Path):
        write(tmp_path, "NOT A SETTING\nEXAMPLE_KEY=kept\n")
        assert load_env_file(tmp_path) == {"EXAMPLE_KEY": "kept"}

    def test_invalid_names_are_skipped(self, tmp_path: Path):
        write(tmp_path, "not-a-name=x\n9LEADING=x\nEXAMPLE_KEY=kept\n")
        assert load_env_file(tmp_path) == {"EXAMPLE_KEY": "kept"}

    def test_later_lines_win_within_the_file(self, tmp_path: Path):
        write(tmp_path, "EXAMPLE_KEY=first\nEXAMPLE_KEY=second\n")
        assert load_env_file(tmp_path)["EXAMPLE_KEY"] == "second"


class TestShippedExample:
    def test_the_example_parses_and_sets_nothing(self, tmp_path: Path):
        example = Path(__file__).resolve().parents[1] / ".env.example"
        (tmp_path / ".env").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        # Every live line is empty or commented, so copying it changes no behaviour.
        assert load_env_file(tmp_path) == {"ANTHROPIC_API_KEY": ""}
