"""Detects a quietly weakened safety net.

Three signals: deleted test files, skip/xfail markers added, and test bodies
gutted of assertions.

Counting assertions beats AST body-matching: a refactor that *moves*
assertions nets to zero and stays silent, so only real loss flags.
"""

from __future__ import annotations

import re

from vouch.checks.shared import is_code_file, is_test_file
from vouch.core.types import CheckContext, Finding

CHECK_NAME = "tests"
CODE = "test-tampering"

# pytest and unittest ways to stop a test running. skipif is included: a
# newly added condition can silently disable a suite.
_SKIP_MARKER = re.compile(
    r"@(?:pytest\.)?mark\.(?:skip|skipif|xfail)\b"
    r"|\bpytest\.skip\s*\("
    r"|\bpytest\.xfail\s*\("
    r"|@unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b"
    r"|\bself\.skipTest\s*\("
)

# bare asserts, unittest methods, and pytest.raises context managers
_ASSERTION = re.compile(
    r"^\s*assert\b"
    r"|\bself\.assert[A-Za-z]*\s*\("
    r"|\bpytest\.raises\s*\("
    r"|\bpytest\.warns\s*\("
    r"|\bassertRaises\s*\("
)

_SKIP_EFFECT = {
    "xfail": "test expected to fail",
    "skipif": "test conditionally disabled",
}


def run(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    deleted_reported: set[str] = set()
    tally: dict[str, dict[str, int]] = {}

    for hunk in ctx.hunks:
        if hunk.status == "binary" or not is_code_file(hunk.file):
            continue
        if not is_test_file(hunk.file):
            continue

        if hunk.status == "deleted":
            # The strongest finding for this file; also counting its lost
            # assertions would double-report the same loss.
            if hunk.file not in deleted_reported:
                deleted_reported.add(hunk.file)
                findings.append(
                    Finding(
                        check=CHECK_NAME,
                        code=CODE,
                        severity="error",
                        file=hunk.file,
                        message="test file deleted",
                        confidence="high",
                    )
                )
            continue

        for line in hunk.lines:
            if line.kind == "add":
                marker = _SKIP_MARKER.search(line.text)
                if marker:
                    findings.append(
                        Finding(
                            check=CHECK_NAME,
                            code=CODE,
                            severity="warn",
                            file=hunk.file,
                            line=line.new_line,
                            message=(
                                f"{marker.group(0).rstrip('(').strip()} added "
                                f"({_effect_of(marker.group(0))}): \"{line.text.strip()}\""
                            ),
                            confidence="high",
                        )
                    )
            if line.kind != "context" and _ASSERTION.search(line.text):
                counts = tally.setdefault(hunk.file, {"del": 0, "add": 0})
                counts["del" if line.kind == "del" else "add"] += 1

    for file, counts in tally.items():
        if counts["del"] > counts["add"]:
            findings.append(
                Finding(
                    check=CHECK_NAME,
                    code=CODE,
                    severity="warn",
                    file=file,
                    message=(
                        f"assertions removed: {counts['del']} deleted, "
                        f"{counts['add']} added in this diff"
                    ),
                    confidence="medium",
                )
            )
    return findings


def _effect_of(marker: str) -> str:
    for needle, effect in _SKIP_EFFECT.items():
        if needle in marker:
            return effect
    return "test disabled"
