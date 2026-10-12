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


def resolve_task(
    repo_root: str | Path,
    message: str | None = None,
    *,
    transcript_loader=None,
    prompt: bool = True,
) -> TaskInfo:
    """Resolve the task text and record where it came from.

    ``transcript_loader`` is an optional zero-arg callable returning text or
    ``None``; it must never raise into this function.
    """
    if message and message.strip():
        return TaskInfo(text=message.strip(), source="flag")

    from_file = read_task_file(repo_root)
    if from_file:
        return TaskInfo(text=from_file, source="taskfile")

    if transcript_loader is not None:
        try:
            guessed = transcript_loader()
        except Exception:
            guessed = None  # best-effort by contract; never break the run
        if guessed and guessed.strip():
            return TaskInfo(text=guessed.strip(), source="transcript")

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
