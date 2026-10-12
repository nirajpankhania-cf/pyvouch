"""The agent's read-only toolset.

A verifier that mutates the repo under review is a contradiction, so every
tool here only reads, and every path is jailed to the repo root.

``execute_tool`` never raises. All failures become ``ERROR: ...`` strings, so a
bad call costs the model one turn instead of ending the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vouch.agent.secrets import holds_secrets
from vouch.context.gitcmd import GitError, run_git

MAX_FILE_LINES = 200
MAX_DIR_ENTRIES = 200
MAX_SEARCH_LINES = 50
MAX_LOG_ENTRIES = 10

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": (
            "Read a file from the repository to see the context around a hunk. Returns up to "
            f"{MAX_FILE_LINES} numbered lines. Optionally pass a 1-based line range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path (forward slashes).",
                },
                "start_line": {"type": "integer", "description": "1-based first line (optional)."},
                "end_line": {
                    "type": "integer",
                    "description": "1-based last line, inclusive (optional).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_git_log",
        "description": (
            "Recent commit history of a file — was this churn normal? Returns up to "
            f"{MAX_LOG_ENTRIES} commits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Repo-relative file path."}},
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the entries of a directory to orient in the repo structure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative directory path. Defaults to the repo root.",
                }
            },
        },
    },
    {
        "name": "search",
        "description": (
            "Search tracked files for a pattern (git grep) — e.g. to check whether a function is "
            f"actually used. Returns up to {MAX_SEARCH_LINES} matching lines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text or basic regex to search for."}
            },
            "required": ["pattern"],
        },
    },
]

TOOL_NAMES: frozenset[str] = frozenset(tool["name"] for tool in TOOL_DEFS)


def execute_tool(name: str, raw_input: Any, repo_root: str | Path) -> str:
    """Run one tool call. Never raises."""
    try:
        if not isinstance(raw_input, dict):
            return f"ERROR: invalid input for {name}: expected an object"
        if name == "read_file":
            return _read_file(raw_input, repo_root)
        if name == "read_git_log":
            return _read_git_log(raw_input, repo_root)
        if name == "list_dir":
            return _list_dir(raw_input, repo_root)
        if name == "search":
            return _search(raw_input, repo_root)
        return f"ERROR: unknown tool: {name}"
    except Exception as exc:  # the loop must survive any tool failure
        return f"ERROR: {exc}"


def _resolve_in_repo(repo_root: str | Path, candidate: str) -> Path | None:
    """Resolve a repo-relative path, or None if it escapes the root."""
    root = Path(repo_root).resolve()
    try:
        resolved = (root / candidate).resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' must be a non-empty string")
    return value


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"'{key}' must be a number")
    number = int(value)
    return number if number >= 1 else None


def _read_file(data: dict[str, Any], repo_root: str | Path) -> str:
    raw_path = _require_str(data, "path")
    resolved = _resolve_in_repo(repo_root, raw_path)
    if resolved is None:
        return f"ERROR: path escapes repository root: {raw_path}"
    if resolved.is_dir():
        return f"ERROR: not a file: {raw_path}"
    if holds_secrets(raw_path):
        # The jail stops paths leaving the repo; this stops credentials
        # leaving the machine. Classify it by its path instead.
        return f"ERROR: refusing to read a credentials file: {raw_path}"

    text = resolved.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    start = _optional_int(data, "start_line") or 1
    requested_end = _optional_int(data, "end_line") or start + MAX_FILE_LINES - 1
    end = min(requested_end, start + MAX_FILE_LINES - 1)

    window = lines[start - 1 : end]
    if not window:
        return f"(no lines in range {start}-{end}; file has {len(lines)} lines)"
    return "\n".join(f"{start + offset}\t{line}" for offset, line in enumerate(window))


def _read_git_log(data: dict[str, Any], repo_root: str | Path) -> str:
    raw_path = _require_str(data, "path")
    if _resolve_in_repo(repo_root, raw_path) is None:
        return f"ERROR: path escapes repository root: {raw_path}"
    try:
        out = run_git(
            [
                "log",
                f"-{MAX_LOG_ENTRIES}",
                "--date=short",
                "--format=%h %ad %s",
                "--",
                raw_path,
            ],
            repo_root,
        )
    except GitError as exc:
        return f"ERROR: {exc}"
    return out.strip() or f"(no commit history for {raw_path})"


def _list_dir(data: dict[str, Any], repo_root: str | Path) -> str:
    raw_path = data.get("path") or "."
    if not isinstance(raw_path, str):
        return "ERROR: 'path' must be a string"
    resolved = _resolve_in_repo(repo_root, raw_path)
    if resolved is None:
        return f"ERROR: path escapes repository root: {raw_path}"
    if not resolved.is_dir():
        return f"ERROR: not a directory: {raw_path}"

    entries = sorted(
        f"{entry.name}/" if entry.is_dir() else entry.name for entry in resolved.iterdir()
    )
    if not entries:
        return "(empty directory)"
    shown = entries[:MAX_DIR_ENTRIES]
    if len(entries) > MAX_DIR_ENTRIES:
        shown.append(f"… ({len(entries) - MAX_DIR_ENTRIES} more entries)")
    return "\n".join(shown)


def _search(data: dict[str, Any], repo_root: str | Path) -> str:
    pattern = _require_str(data, "pattern")
    try:
        # git grep exits 1 when nothing matches, which is not an error here.
        out = run_git(["grep", "-n", "-I", "-e", pattern], repo_root, check=False)
    except GitError as exc:
        return f"ERROR: {exc}"

    # A match line carries the matching content, so searching is another way
    # to read a secret.
    lines = [
        line
        for line in out.split("\n")
        if line and not holds_secrets(line.split(":", 1)[0])
    ]
    if not lines:
        return f"(no matches for: {pattern})"
    shown = "\n".join(lines[:MAX_SEARCH_LINES])
    if len(lines) > MAX_SEARCH_LINES:
        return f"{shown}\n… ({len(lines) - MAX_SEARCH_LINES} more matches)"
    return shown
