"""The agent toolset: read-only, jailed to the repo, and never raising."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vouch.agent.tools import (
    MAX_FILE_LINES,
    MAX_SEARCH_LINES,
    TOOL_DEFS,
    TOOL_NAMES,
    execute_tool,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "app.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "initial commit")
    (tmp_path / "secret_outside.txt").write_text("x", encoding="utf-8")
    return tmp_path


class TestToolDefinitions:
    def test_the_four_documented_tools_exist(self):
        assert {"read_file", "read_git_log", "list_dir", "search"} == TOOL_NAMES

    def test_every_tool_declares_a_schema(self):
        for tool in TOOL_DEFS:
            assert tool["input_schema"]["type"] == "object"
            assert tool["description"]

    def test_no_tool_can_write(self):
        # The toolset is the enforcement point for "the verifier never mutates".
        assert not any(
            word in tool["name"] for tool in TOOL_DEFS for word in ("write", "edit", "delete")
        )


class TestReadFile:
    def test_reads_numbered_lines(self, repo: Path):
        out = execute_tool("read_file", {"path": "app.py"}, repo)
        assert out.splitlines()[0] == "1\tdef one():"

    def test_respects_a_line_range(self, repo: Path):
        out = execute_tool("read_file", {"path": "app.py", "start_line": 2, "end_line": 2}, repo)
        assert out == "2\t    return 1"

    def test_caps_the_number_of_lines(self, repo: Path):
        (repo / "big.py").write_text("\n".join(f"x = {n}" for n in range(500)), encoding="utf-8")
        out = execute_tool("read_file", {"path": "big.py"}, repo)
        assert len(out.splitlines()) == MAX_FILE_LINES

    def test_end_line_cannot_exceed_the_cap(self, repo: Path):
        (repo / "big.py").write_text("\n".join(f"x = {n}" for n in range(500)), encoding="utf-8")
        out = execute_tool("read_file", {"path": "big.py", "start_line": 1, "end_line": 400}, repo)
        assert len(out.splitlines()) == MAX_FILE_LINES

    def test_range_past_end_of_file_explains_itself(self, repo: Path):
        out = execute_tool("read_file", {"path": "app.py", "start_line": 99}, repo)
        assert "no lines in range" in out

    def test_missing_file_is_an_error_string(self, repo: Path):
        out = execute_tool("read_file", {"path": "nope.py"}, repo)
        assert out.startswith("ERROR:")

    def test_directory_is_an_error_string(self, repo: Path):
        assert execute_tool("read_file", {"path": "pkg"}, repo).startswith("ERROR: not a file")

    def test_binary_content_does_not_raise(self, repo: Path):
        (repo / "blob.bin").write_bytes(b"\x00\xff\xfe")
        assert not execute_tool("read_file", {"path": "blob.bin"}, repo).startswith("ERROR")


class TestJail:
    @pytest.mark.parametrize(
        "escape",
        ["../outside.txt", "../../etc/passwd", "pkg/../../outside.txt", "/etc/passwd"],
    )
    def test_paths_cannot_escape_the_repo_root(self, repo: Path, escape: str):
        out = execute_tool("read_file", {"path": escape}, repo)
        assert "escapes repository root" in out

    def test_list_dir_cannot_escape(self, repo: Path):
        assert "escapes repository root" in execute_tool("list_dir", {"path": ".."}, repo)

    def test_git_log_cannot_escape(self, repo: Path):
        out = execute_tool("read_git_log", {"path": "../../etc/passwd"}, repo)
        assert "escapes repository root" in out

    def test_paths_inside_the_repo_are_allowed(self, repo: Path):
        assert not execute_tool("read_file", {"path": "pkg/mod.py"}, repo).startswith("ERROR")

    def test_the_root_itself_is_allowed(self, repo: Path):
        assert not execute_tool("list_dir", {"path": "."}, repo).startswith("ERROR")


class TestListDir:
    def test_lists_entries_with_directory_markers(self, repo: Path):
        out = execute_tool("list_dir", {}, repo)
        assert "app.py" in out
        assert "pkg/" in out

    def test_defaults_to_the_repo_root(self, repo: Path):
        assert execute_tool("list_dir", {}, repo) == execute_tool("list_dir", {"path": "."}, repo)

    def test_empty_directory_says_so(self, repo: Path):
        (repo / "hollow").mkdir()
        assert execute_tool("list_dir", {"path": "hollow"}, repo) == "(empty directory)"

    def test_file_instead_of_directory_is_an_error(self, repo: Path):
        out = execute_tool("list_dir", {"path": "app.py"}, repo)
        assert out.startswith("ERROR: not a directory")


class TestGitLog:
    def test_returns_commit_history(self, repo: Path):
        out = execute_tool("read_git_log", {"path": "app.py"}, repo)
        assert "initial commit" in out

    def test_untracked_file_has_no_history(self, repo: Path):
        out = execute_tool("read_git_log", {"path": "secret_outside.txt"}, repo)
        assert "no commit history" in out


class TestSearch:
    def test_finds_matches_with_line_numbers(self, repo: Path):
        out = execute_tool("search", {"pattern": "VALUE"}, repo)
        assert "pkg/mod.py:1" in out

    def test_no_match_is_not_an_error(self, repo: Path):
        # git grep exits non-zero on no match; that isn't a failure here.
        out = execute_tool("search", {"pattern": "zzz_not_present"}, repo)
        assert out == "(no matches for: zzz_not_present)"

    def test_results_are_capped(self, repo: Path):
        body = "\n".join("needle = 1" for _ in range(MAX_SEARCH_LINES + 20))
        (repo / "many.py").write_text(body, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        out = execute_tool("search", {"pattern": "needle"}, repo)
        assert "more matches" in out


class TestNeverRaises:
    def test_unknown_tool_returns_an_error_string(self, repo: Path):
        assert execute_tool("rm_rf", {}, repo) == "ERROR: unknown tool: rm_rf"

    def test_missing_required_argument(self, repo: Path):
        assert execute_tool("read_file", {}, repo).startswith("ERROR:")

    def test_wrong_argument_type(self, repo: Path):
        assert execute_tool("read_file", {"path": 42}, repo).startswith("ERROR:")

    def test_non_object_input(self, repo: Path):
        assert execute_tool("read_file", "just a string", repo).startswith("ERROR:")

    def test_bad_line_numbers_do_not_raise(self, repo: Path):
        assert not execute_tool("read_file", {"path": "app.py", "start_line": -5}, repo).startswith(
            "ERROR"
        )

    def test_non_numeric_line_is_an_error_string(self, repo: Path):
        out = execute_tool("read_file", {"path": "app.py", "start_line": "two"}, repo)
        assert out.startswith("ERROR:")

    def test_search_without_a_pattern(self, repo: Path):
        assert execute_tool("search", {}, repo).startswith("ERROR:")

    def test_tools_outside_a_git_repo_degrade(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        # read_file still works; git-backed tools report an error, not a crash.
        assert not execute_tool("read_file", {"path": "a.py"}, tmp_path).startswith("ERROR")
        assert execute_tool("search", {"pattern": "x"}, tmp_path).startswith(
            ("ERROR", "(no matches")
        )
