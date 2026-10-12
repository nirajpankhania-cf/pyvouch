"""Shared test helpers for building check contexts without touching git."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vouch.core.types import CheckContext, Hunk, HunkLine, ProjectAccess, TaskInfo


def whole_file_hunk(file: str, source: str, status: str = "added") -> Hunk:
    """One hunk covering a file, every line marked as added."""
    lines = source.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return Hunk(
        file=file,
        status=status,
        old_start=0,
        old_lines=0,
        new_start=1,
        new_lines=len(lines),
        lines=[
            HunkLine(kind="add", text=text, new_line=number)
            for number, text in enumerate(lines, start=1)
        ],
    )


def make_context(
    task: str = "do the thing",
    sources: dict[str, str] | None = None,
    *,
    hunks: list[Hunk] | None = None,
    installed: set[str] | None = None,
    env_discovered: bool = True,
    module_files: dict[str, str] | None = None,
    module_exports: dict[str, set[str]] | None = None,
    opaque_modules: set[str] | None = None,
) -> CheckContext:
    sources = sources or {}
    if hunks is None:
        hunks = [whole_file_hunk(file, source) for file, source in sources.items()]
    return CheckContext(
        hunks=hunks,
        task=TaskInfo(text=task, source="flag"),
        project=ProjectAccess(
            repo_root="/repo",
            files=dict(sources),
            installed_modules=installed if installed is not None else {"os", "sys", "json"},
            env_discovered=env_discovered,
            module_files=module_files or {},
            module_exports=module_exports or {},
            opaque_modules=opaque_modules or set(),
        ),
    )


def codes(findings) -> list[str]:
    return [finding.code for finding in findings]


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real repo with one commit, for end-to-end pipeline tests."""

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "initial")
    return tmp_path
