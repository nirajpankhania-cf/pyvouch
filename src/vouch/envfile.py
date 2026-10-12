"""Minimal ``.env`` loading.

Enough to pick up an API key from a file instead of a shell profile. Stdlib
only, and deliberately not a general dotenv implementation: no interpolation,
no multi-line values, no `.env.local` cascade.

A real environment variable always wins — an explicit `export` in the shell
should not be silently overridden by a file on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE_VAR = "VOUCH_ENV_FILE"
DEFAULT_NAME = ".env"


def load_env_file(cwd: str | Path | None = None) -> dict[str, str]:
    """Load ``.env`` into ``os.environ``. Returns the names actually set.

    Missing or unreadable files are not an error: the agentic pass already
    degrades cleanly when no key is present.
    """
    path = _locate(cwd)
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    applied: dict[str, str] = {}
    for key, value in _parse(text).items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def _locate(cwd: str | Path | None) -> Path | None:
    override = os.environ.get(ENV_FILE_VAR)
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    candidate = Path(cwd or Path.cwd()) / DEFAULT_NAME
    return candidate if candidate.is_file() else None


def _parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        if not key.isidentifier():
            continue
        values[key] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    # An unquoted trailing comment is not part of the value.
    return value.split(" #", 1)[0].strip()
