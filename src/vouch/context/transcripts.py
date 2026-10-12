"""Read the task straight out of a coding agent's transcript.

Retyping what you just asked an agent is both tedious and a chance to
accidentally restate the task more charitably than you phrased it, which is
exactly the thing vouch should not help you do.

Two formats, both newline-delimited JSON written per project directory:

* **Cursor CLI** — ``~/.cursor/projects/<encoded>/agent-transcripts/<id>/<id>.jsonl``.
  Records are ``{"role": ..., "message": {"content": [blocks]}}`` and a user's
  real words are wrapped in ``<user_query>`` inside a text block.
* **Claude Code** — ``~/.claude/projects/<encoded>/<id>.jsonl``. Records are
  ``{"type": "user", "message": {"role": "user", "content": ...}}`` where
  content is a string or a list of blocks.

Parsing is best effort and total: an unreadable or reshaped transcript yields
no prompts rather than an error. These are other tools' private files and may
change shape without notice, so nothing here is allowed to break a run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CURSOR_ROOT = Path.home() / ".cursor" / "projects"
CLAUDE_ROOT = Path.home() / ".claude" / "projects"

# Cursor wraps the human's words; everything else in the block is harness noise.
_USER_QUERY = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)
# Tags the harness injects around a prompt, none of which are the task.
_NOISE_TAGS = re.compile(
    r"<(timestamp|system_reminder|attached_files|system_notification|"
    r"environment_details|additional_data)>.*?</\1>",
    re.DOTALL,
)

# A transcript's trailing message is often "yes", "go on", "thanks". Those are
# replies to the agent, not statements of intent, and judging a diff against
# one would be worse than asking.
_MIN_PROMPT_CHARS = 12
_ACKNOWLEDGEMENTS = frozenset(
    {
        "carry on",
        "continue",
        "do it",
        "go",
        "go ahead",
        "k",
        "keep going",
        "lgtm",
        "next",
        "no",
        "ok",
        "okay",
        "perfect",
        "proceed",
        "sounds good",
        "sure",
        "thanks",
        "thank you",
        "ty",
        "y",
        "yep",
        "yes",
        "yh",
        "yup",
    }
)

MAX_PROMPTS = 20
# Transcripts reach megabytes; only the tail can hold the newest prompt.
_MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Prompt:
    """One thing a human typed to an agent, newest first when listed."""

    text: str
    tool: str
    session: str
    modified: float

    @property
    def summary(self) -> str:
        """A single line, for a picker or a confirmation prompt."""
        line = " ".join(self.text.split())
        return line if len(line) <= 120 else f"{line[:117]}…"


def encode_cursor_dir(cwd: str | Path) -> str:
    """``/Users/me/dev/app`` → ``Users-me-dev-app``."""
    return _encode(cwd)


def encode_claude_dir(cwd: str | Path) -> str:
    """``/Users/me/dev/app`` → ``-Users-me-dev-app`` (leading separator kept)."""
    return f"-{_encode(cwd)}"


def _encode(cwd: str | Path) -> str:
    resolved = str(Path(cwd).resolve())
    return resolved.lstrip("/").replace("/", "-").replace(".", "-").replace("_", "-")


def recent_prompts(cwd: str | Path, limit: int = MAX_PROMPTS) -> list[Prompt]:
    """Human prompts for this directory, newest first. Never raises."""
    collected: list[Prompt] = []
    for path, tool in _transcript_files(cwd):
        try:
            collected.extend(_prompts_in(path, tool))
        except Exception:
            continue  # another tool's format; skip it, never fail the run
    collected.sort(key=lambda prompt: prompt.modified, reverse=True)
    return _dedup(collected)[:limit]


def latest_prompt(cwd: str | Path) -> str | None:
    """The most recent substantive prompt, for ``resolve_task``."""
    found = recent_prompts(cwd, limit=1)
    return found[0].text if found else None


def _transcript_files(cwd: str | Path) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    cursor = CURSOR_ROOT / encode_cursor_dir(cwd) / "agent-transcripts"
    if cursor.is_dir():
        found += [(path, "cursor") for path in cursor.glob("*/*.jsonl")]
        found += [(path, "cursor") for path in cursor.glob("*.jsonl")]
    claude = CLAUDE_ROOT / encode_claude_dir(cwd)
    if claude.is_dir():
        found += [(path, "claude-code") for path in claude.glob("*.jsonl")]
    return found


def _prompts_in(path: Path, tool: str) -> list[Prompt]:
    modified = path.stat().st_mtime
    session = path.stem
    prompts: list[Prompt] = []
    for record in _records(path):
        text = _human_text(record, tool)
        if text and _is_substantive(text):
            prompts.append(Prompt(text=text, tool=tool, session=session, modified=modified))
    # Within one file the last prompt is the newest; the caller sorts files.
    prompts.reverse()
    return prompts


def _records(path: Path):
    if path.stat().st_size > _MAX_BYTES:
        return
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # a truncated tail is normal while a session runs
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _human_text(record: dict, tool: str) -> str | None:
    if _role_of(record) != "user":
        return None
    if record.get("isMeta") or record.get("isSidechain"):
        return None  # Claude Code's synthetic turns
    origin = record.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return None

    raw = _content_text(record)
    if not raw:
        return None

    if tool == "cursor":
        matches = _USER_QUERY.findall(raw)
        if not matches:
            # No marker means harness scaffolding, not something typed.
            return None
        raw = matches[-1]

    return _clean(raw)


def _role_of(record: dict) -> str | None:
    message = record.get("message")
    if isinstance(message, dict) and message.get("role"):
        return str(message["role"])
    role = record.get("role") or record.get("type")
    return str(role) if role else None


def _content_text(record: dict) -> str:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else record.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(
            block.get("text"), str
        )
    ]
    return "\n".join(parts)


def _clean(text: str) -> str:
    return _NOISE_TAGS.sub("", text).strip()


def _is_substantive(text: str) -> bool:
    """Filter replies to the agent from statements of intent."""
    normalized = " ".join(text.lower().split()).strip(" .!?,")
    return normalized not in _ACKNOWLEDGEMENTS and len(normalized) >= _MIN_PROMPT_CHARS


def _dedup(prompts: list[Prompt]) -> list[Prompt]:
    seen: set[str] = set()
    unique: list[Prompt] = []
    for prompt in prompts:
        key = " ".join(prompt.text.split())
        if key not in seen:
            seen.add(key)
            unique.append(prompt)
    return unique


__all__ = [
    "MAX_PROMPTS",
    "Prompt",
    "encode_claude_dir",
    "encode_cursor_dir",
    "latest_prompt",
    "recent_prompts",
]
