"""All git invocation. Read-only by construction: no command here mutates a repo."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """A git call failed or the directory isn't a repo. The CLI maps this to exit 2."""


# core.quotepath=false keeps non-ASCII paths as UTF-8 instead of \NNN escapes.
# color/ext-diff are forced off so parsing never sees ANSI or a user's difftool.
_BASE_FLAGS = ["-c", "core.quotepath=false"]


def run_git(args: list[str], cwd: str | Path, *, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", *_BASE_FLAGS, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH.") from exc
    except OSError as exc:
        raise GitError(f"could not run git: {exc}") from exc

    if check and result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def is_inside_repo(cwd: str | Path) -> bool:
    try:
        return run_git(["rev-parse", "--is-inside-work-tree"], cwd).strip() == "true"
    except GitError:
        return False


def repo_root(cwd: str | Path) -> str:
    return run_git(["rev-parse", "--show-toplevel"], cwd).strip()


def current_commit(cwd: str | Path) -> str | None:
    try:
        return run_git(["rev-parse", "HEAD"], cwd).strip() or None
    except GitError:
        return None  # unborn branch: no commits yet


def current_branch(cwd: str | Path) -> str | None:
    try:
        name = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd).strip()
    except GitError:
        return None
    return name or None


def untracked_files(cwd: str | Path) -> list[str]:
    out = run_git(["ls-files", "--others", "--exclude-standard", "-z"], cwd)
    return [p for p in out.split("\0") if p]


def has_commits(cwd: str | Path) -> bool:
    try:
        run_git(["rev-parse", "--verify", "HEAD"], cwd)
        return True
    except GitError:
        return False
