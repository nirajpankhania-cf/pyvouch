"""Task resolution: ``-m`` → ``TASK.md`` → transcript → interactive prompt.

A task guessed from a transcript is never used silently — the caller echoes it
and, on a TTY, asks for confirmation.
"""

from __future__ import annotations

import sys
from pathlib import Path

from vouch.core.types import TaskInfo

TASK_FILE = "TASK.md"


class TaskError(Exception):
    """No task could be resolved. The CLI maps this to exit 2."""


def read_task_file(repo_root: str | Path) -> str | None:
    path = Path(repo_root) / TASK_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return text.strip() or None


def default_transcript_loader(repo_root: str | Path):
    """The shipped loader: the newest prompt typed to Cursor CLI or Claude Code."""

    def load() -> str | None:
        from vouch.context.transcripts import latest_prompt

        return latest_prompt(repo_root)

    return load


def resolve_task(
    repo_root: str | Path,
    message: str | None = None,
    *,
    transcript_loader=None,
    prompt: bool = True,
    use_transcript: bool = True,
) -> TaskInfo:
    """Resolve the task text and record where it came from.

    ``transcript_loader`` is a zero-arg callable returning text or ``None``.
    It must never raise into this function; the default already swallows.
    Pass ``use_transcript=False`` to skip the step entirely.
    """
    if message and message.strip():
        return TaskInfo(text=message.strip(), source="flag")

    from_file = read_task_file(repo_root)
    if from_file:
        return TaskInfo(text=from_file, source="taskfile")

    loader = transcript_loader
    if loader is None and use_transcript:
        loader = default_transcript_loader(repo_root)

    if loader is not None:
        try:
            guessed = loader()
        except Exception:
            guessed = None  # best-effort by contract; never break the run
        if guessed and guessed.strip():
            guessed = guessed.strip()
            # A transcript task is inferred, not stated. On a TTY the user
            # confirms it; otherwise it is used but always echoed downstream.
            if prompt and sys.stdin.isatty() and not _confirm(guessed):
                typed = _ask()
                if typed:
                    return TaskInfo(text=typed, source="prompt")
                raise TaskError("task not confirmed — pass -m 'what you asked for'.")
            return TaskInfo(text=guessed, source="transcript")

    if prompt and sys.stdin.isatty():
        typed = _ask()
        if typed:
            return TaskInfo(text=typed, source="prompt")

    raise TaskError(
        "no task given — pass -m 'what you asked for', or write it to TASK.md.",
    )


def _ask() -> str | None:
    try:
        answer = input("What did you ask the agent to do? ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return answer or None


def _confirm(guessed: str) -> bool:
    one_line = " ".join(guessed.split())
    shown = one_line if len(one_line) <= 160 else f"{one_line[:157]}…"
    print(f"\nFound this in your agent transcript:\n  {shown}\n")
    try:
        answer = input("Review the diff against that? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("", "y", "yes")
