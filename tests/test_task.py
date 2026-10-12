"""Task resolution order and failure behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from vouch.context.task import TaskError, read_task_file, resolve_task


def write_task(root: Path, text: str) -> None:
    (root / "TASK.md").write_text(text, encoding="utf-8")


class TestPriorityOrder:
    def test_flag_wins(self, tmp_path: Path):
        write_task(tmp_path, "from the file")
        task = resolve_task(tmp_path, message="from the flag")
        assert (task.text, task.source) == ("from the flag", "flag")

    def test_task_file_used_when_no_flag(self, tmp_path: Path):
        write_task(tmp_path, "add retries\n")
        task = resolve_task(tmp_path, prompt=False)
        assert (task.text, task.source) == ("add retries", "taskfile")

    def test_task_file_beats_transcript(self, tmp_path: Path):
        write_task(tmp_path, "the real task")
        task = resolve_task(tmp_path, transcript_loader=lambda: "guessed", prompt=False)
        assert task.source == "taskfile"

    def test_transcript_used_when_nothing_else(self, tmp_path: Path):
        task = resolve_task(tmp_path, transcript_loader=lambda: "guessed task", prompt=False)
        assert (task.text, task.source) == ("guessed task", "transcript")

    def test_blank_flag_falls_through(self, tmp_path: Path):
        write_task(tmp_path, "the file task")
        task = resolve_task(tmp_path, message="   ", prompt=False)
        assert task.source == "taskfile"

    def test_text_is_stripped(self, tmp_path: Path):
        task = resolve_task(tmp_path, message="  padded  ")
        assert task.text == "padded"


class TestTaskFileReading:
    def test_missing_file_is_none(self, tmp_path: Path):
        assert read_task_file(tmp_path) is None

    def test_whitespace_only_file_is_none(self, tmp_path: Path):
        write_task(tmp_path, "   \n\n  ")
        assert read_task_file(tmp_path) is None

    def test_multiline_task_is_preserved(self, tmp_path: Path):
        write_task(tmp_path, "line one\nline two\n")
        assert read_task_file(tmp_path) == "line one\nline two"


class TestFailureModes:
    def test_no_task_anywhere_raises(self, tmp_path: Path):
        with pytest.raises(TaskError, match="no task given"):
            resolve_task(tmp_path, prompt=False)

    def test_transcript_loader_exception_is_swallowed(self, tmp_path: Path):
        def explode() -> str:
            raise RuntimeError("malformed jsonl")

        # Best-effort by contract: a broken loader must never break the run.
        with pytest.raises(TaskError):
            resolve_task(tmp_path, transcript_loader=explode, prompt=False)

    def test_transcript_returning_none_falls_through(self, tmp_path: Path):
        with pytest.raises(TaskError):
            resolve_task(tmp_path, transcript_loader=lambda: None, prompt=False)

    def test_transcript_returning_blank_falls_through(self, tmp_path: Path):
        with pytest.raises(TaskError):
            resolve_task(tmp_path, transcript_loader=lambda: "  ", prompt=False)

    def test_non_tty_does_not_prompt(self, tmp_path: Path, monkeypatch):
        # pytest already redirects stdin, but be explicit: CI must not hang.
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        with pytest.raises(TaskError):
            resolve_task(tmp_path, prompt=True)
