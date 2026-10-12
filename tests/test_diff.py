"""Diff parsing, against fixtures captured from real git output."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vouch.context.diff import (
    DiffError,
    DiffMode,
    _git_diff_args,
    get_hunks,
    parse_diff_to_hunks,
)

FIXTURES = Path(__file__).parent / "fixtures" / "diff"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.diff").read_text(encoding="utf-8")


def hunks_for(name: str):
    return parse_diff_to_hunks(load(name))


class TestStatus:
    def test_modify(self):
        (hunk,) = hunks_for("modify")
        assert (hunk.file, hunk.status, hunk.old_file) == ("calc.py", "modified", None)

    def test_new_file(self):
        (hunk,) = hunks_for("new-file")
        assert (hunk.file, hunk.status) == ("added.py", "added")

    def test_delete_keeps_the_old_path(self):
        (hunk,) = hunks_for("delete")
        assert (hunk.file, hunk.status) == ("doomed.txt", "deleted")

    def test_pure_rename_has_no_hunk_body(self):
        # 100% similarity: git emits rename headers and no ---/+++ pair.
        (hunk,) = hunks_for("rename")
        assert hunk.status == "renamed"
        assert hunk.file == "moved.txt"
        assert hunk.old_file == "tomove.txt"
        assert hunk.lines == []

    def test_binary_is_flagged_not_parsed(self):
        (hunk,) = hunks_for("binary")
        assert (hunk.file, hunk.status, hunk.lines) == ("blob.bin", "binary", [])

    def test_mode_only_change_is_surfaced(self):
        # No content and no lines, but still a change worth reporting.
        (hunk,) = hunks_for("mode-only")
        assert (hunk.file, hunk.status, hunk.lines) == ("calc.py", "modified", [])

    def test_path_containing_spaces(self):
        (hunk,) = hunks_for("spaces-in-path")
        assert hunk.file == "my file.py"


class TestLineAccounting:
    def test_modify_line_numbers_and_kinds(self):
        (hunk,) = hunks_for("modify")
        added = hunk.added_lines()
        removed = hunk.removed_lines()
        assert [ln.text for ln in added] == ["    return 99"]
        assert [ln.text for ln in removed] == ["    return 1"]
        assert added[0].new_line == 2
        assert removed[0].old_line == 2

    def test_context_lines_carry_both_numbers(self):
        (hunk,) = hunks_for("modify")
        context = [ln for ln in hunk.lines if ln.kind == "context"]
        assert context
        for line in context:
            assert line.old_line is not None
            assert line.new_line is not None

    def test_line_numbers_increase_monotonically(self):
        for hunk in hunks_for("multi-hunk"):
            new_numbers = [ln.new_line for ln in hunk.lines if ln.new_line is not None]
            old_numbers = [ln.old_line for ln in hunk.lines if ln.old_line is not None]
            assert new_numbers == sorted(new_numbers)
            assert old_numbers == sorted(old_numbers)

    def test_multi_hunk_produces_one_hunk_per_region(self):
        hunks = hunks_for("multi-hunk")
        assert len(hunks) == 2
        assert {h.file for h in hunks} == {"long.txt"}
        assert hunks[0].new_start < hunks[1].new_start

    def test_omitted_counts_default_to_one(self):
        # Header is "@@ -1 +1 @@" with no counts.
        (hunk,) = hunks_for("no-newline")
        assert (hunk.old_lines, hunk.new_lines) == (1, 1)

    def test_no_newline_marker_is_not_content(self):
        (hunk,) = hunks_for("no-newline")
        assert not any(ln.text.startswith("\\") for ln in hunk.lines)
        assert [ln.text for ln in hunk.added_lines()] == ["changed, still no newline"]

    def test_new_file_lines_are_all_additions(self):
        (hunk,) = hunks_for("new-file")
        assert all(ln.kind == "add" for ln in hunk.lines)
        assert hunk.old_lines == 0

    def test_deleted_file_lines_are_all_removals(self):
        (hunk,) = hunks_for("delete")
        assert all(ln.kind == "del" for ln in hunk.lines)
        assert hunk.new_lines == 0


class TestMixedDiff:
    def test_one_file_section_each(self):
        hunks = hunks_for("mixed")
        by_file = {h.file: h.status for h in hunks}
        assert by_file["calc.py"] == "modified"
        assert by_file["newthing.py"] == "added"
        assert by_file["long.txt"] == "deleted"

    def test_hunks_never_leak_across_files(self):
        for hunk in hunks_for("mixed"):
            for line in hunk.lines:
                assert line.text is not None
            assert hunk.file


class TestRangeProperty:
    def test_range_spans_the_new_side(self):
        (hunk,) = hunks_for("modify")
        start, end = hunk.range.split("-")
        assert int(end) - int(start) + 1 == hunk.new_lines

    def test_zero_new_lines_renders_a_single_number(self):
        (hunk,) = hunks_for("delete")
        assert "-" not in hunk.range


class TestEmptyAndMalformed:
    def test_empty_text_yields_nothing(self):
        assert parse_diff_to_hunks("") == []
        assert parse_diff_to_hunks("   \n  \n") == []

    def test_text_without_a_file_header_is_ignored(self):
        assert parse_diff_to_hunks("@@ -1 +1 @@\n-a\n+b\n") == []

    def test_truncated_section_does_not_raise(self):
        assert parse_diff_to_hunks("diff --git a/x.py b/x.py\n") != []


class TestDiffModeArgs:
    def test_working_diffs_against_head(self):
        assert _git_diff_args(DiffMode.working())[-1] == "HEAD"

    def test_staged_uses_cached(self):
        assert "--cached" in _git_diff_args(DiffMode.staged())

    def test_base_uses_three_dot_merge_base_semantics(self):
        assert _git_diff_args(DiffMode.base("main"))[-1] == "main...HEAD"

    def test_renames_are_detected(self):
        assert "--find-renames" in _git_diff_args(DiffMode.working())


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "seed.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "seed")
    return tmp_path


class TestAgainstLiveGit:
    def test_not_a_repo_raises_diff_error(self, tmp_path: Path):
        with pytest.raises(DiffError, match="not a git repository"):
            get_hunks(DiffMode.working(), tmp_path)

    def test_clean_repo_has_no_hunks(self, temp_repo: Path):
        assert get_hunks(DiffMode.working(), temp_repo) == []

    def test_working_mode_sees_modifications(self, temp_repo: Path):
        (temp_repo / "seed.py").write_text("x = 2\n", encoding="utf-8")
        (hunk,) = get_hunks(DiffMode.working(), temp_repo)
        assert hunk.file == "seed.py"
        assert [ln.text for ln in hunk.added_lines()] == ["x = 2"]

    def test_working_mode_synthesizes_untracked_files(self, temp_repo: Path):
        # git diff HEAD cannot see these, but a file an agent just wrote is
        # exactly what vouch exists to inspect.
        (temp_repo / "brand_new.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        hunks = get_hunks(DiffMode.working(), temp_repo)
        (new_hunk,) = [h for h in hunks if h.file == "brand_new.py"]
        assert new_hunk.status == "added"
        assert [ln.text for ln in new_hunk.lines] == ["a = 1", "b = 2"]
        assert new_hunk.new_lines == 2

    def test_untracked_binary_is_flagged_not_decoded(self, temp_repo: Path):
        (temp_repo / "thing.bin").write_bytes(b"\x00\x01\x02")
        hunks = get_hunks(DiffMode.working(), temp_repo)
        (binary,) = [h for h in hunks if h.file == "thing.bin"]
        assert binary.status == "binary"
        assert binary.lines == []

    def test_staged_mode_ignores_untracked(self, temp_repo: Path):
        (temp_repo / "untracked.py").write_text("y = 1\n", encoding="utf-8")
        assert get_hunks(DiffMode.staged(), temp_repo) == []

    def test_staged_mode_sees_only_the_index(self, temp_repo: Path):
        (temp_repo / "staged.py").write_text("s = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "staged.py"], cwd=temp_repo, check=True)
        (temp_repo / "unstaged.py").write_text("u = 1\n", encoding="utf-8")
        files = {h.file for h in get_hunks(DiffMode.staged(), temp_repo)}
        assert files == {"staged.py"}

    def test_bad_base_ref_is_a_friendly_error(self, temp_repo: Path):
        with pytest.raises(DiffError, match="could not resolve --base"):
            get_hunks(DiffMode.base("no-such-branch"), temp_repo)

    def test_gitignored_files_are_not_synthesized(self, temp_repo: Path):
        (temp_repo / ".gitignore").write_text("secret.py\n", encoding="utf-8")
        (temp_repo / "secret.py").write_text("token = 1\n", encoding="utf-8")
        files = {h.file for h in get_hunks(DiffMode.working(), temp_repo)}
        assert "secret.py" not in files
