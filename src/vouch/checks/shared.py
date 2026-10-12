"""Helpers shared by checks.

The file-type gate matters: without it, placeholders and tests flag committed
``.diff`` fixtures and prose, where a TODO is not a stub.
"""

from __future__ import annotations

import ast
import re

from vouch.core.types import Hunk

# Content that is prose or data, not executable code.
_NON_CODE = re.compile(
    r"\.(?:diff|patch|md|markdown|rst|txt|csv|lock|svg|po|pot|ipynb)$"
    r"|(?:^|/)[^/]*lock\.(?:json|ya?ml|toml)$",
    re.IGNORECASE,
)

_TEST_PATH = re.compile(r"(?:^|/)tests?/|(?:^|/)__tests__/")
_TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]+|[^/]+_test|conftest)\.py$")


def is_code_file(git_path: str) -> bool:
    return not _NON_CODE.search(git_path)


def is_python_file(git_path: str) -> bool:
    return git_path.endswith((".py", ".pyi"))


def is_test_file(git_path: str) -> bool:
    """pytest and unittest conventions: tests/ dirs, test_*.py, *_test.py, conftest.py."""
    return bool(_TEST_PATH.search(git_path) or _TEST_FILE.search(git_path))


def added_line_numbers(hunks: list[Hunk], file: str) -> set[int]:
    """New-side line numbers added to ``file`` across all its hunks."""
    return {
        line.new_line
        for hunk in hunks
        if hunk.file == file
        for line in hunk.lines
        if line.kind == "add" and line.new_line is not None
    }


def parse_python(source: str) -> ast.Module | None:
    """Parse source, or return None if it doesn't compile.

    A syntax error is not our finding to report — the user's own tooling will
    say so more clearly, and a half-written file is common mid-edit.
    """
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


def files_in(hunks: list[Hunk], *, python_only: bool = False) -> list[str]:
    """Distinct files in the diff, skipping binaries and non-code, in diff order."""
    seen: list[str] = []
    for hunk in hunks:
        if hunk.status == "binary" or hunk.file in seen:
            continue
        if not is_code_file(hunk.file):
            continue
        if python_only and not is_python_file(hunk.file):
            continue
        seen.append(hunk.file)
    return seen
