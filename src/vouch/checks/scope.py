"""Heuristic scope-creep detector.

Flags files that share *no* tokens with the task text, judged from path
segments and added-line identifiers. Findings are always ``info``/``low``:
real semantic judgment is Pass 2's job, and this only catches the blunt case
where the task says one thing and the diff touches something unrelated.
"""

from __future__ import annotations

import re

from vouch.core.types import CheckContext, Finding

CHECK_NAME = "scope"
CODE = "scope-drift"

# Words common enough in task phrasing to carry no scope signal.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "when",
        "all", "fix", "add", "adds", "added", "update", "updates", "make",
        "change", "changes", "implement", "support", "use", "using", "bug",
        "issue", "error", "should", "not", "now", "new", "can", "src", "lib",
        "init", "main", "test", "tests", "run", "get", "set", "also", "but",
        "its", "where", "which", "while", "have", "has", "been", "was", "are",
        "def", "self", "return", "import",
    }
)

# Files that legitimately ride along with most tasks; flagging them is noise.
_INFRA = re.compile(
    "|".join(
        [
            r"(^|/)pyproject\.toml$",
            r"(^|/)setup\.(py|cfg)$",
            r"(^|/)requirements[^/]*\.txt$",
            r"(^|/)(poetry|uv)\.lock$",
            r"(^|/)Pipfile(\.lock)?$",
            r"(^|/)package(-lock)?\.json$",
            r"(^|/)(pnpm-lock\.yaml|yarn\.lock)$",
            r"(^|/)\.git(ignore|attributes)$",
            r"(^|/)readme(\.[a-z]+)?$",
            r"(^|/)licen[sc]e[^/]*$",
            r"(^|/)changelog(\.[a-z]+)?$",
            r"(^|/)\.github/",
            r"(^|/)Dockerfile$",
            r"(^|/)docker-compose\.ya?ml$",
            r"(^|/)\.vouch\.json$",
            r"(^|/)tox\.ini$",
            r"(^|/)\.pre-commit-config\.ya?ml$",
        ]
    ),
    re.IGNORECASE,
)

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_SPLIT = re.compile(r"[^a-z0-9]+")


def run(ctx: CheckContext) -> list[Finding]:
    task_tokens = tokenize(ctx.task.text)
    # "fix it" carries no scope signal; refusing to judge beats guessing.
    if len(task_tokens) < 2:
        return []

    # Evidence accumulates per file: the path is constant, added lines add up.
    evidence: dict[str, list[str]] = {}
    for hunk in ctx.hunks:
        if _INFRA.search(hunk.file):
            continue
        parts = evidence.setdefault(hunk.file, [hunk.file, hunk.old_file or ""])
        parts.extend(line.text for line in hunk.lines if line.kind == "add")

    findings: list[Finding] = []
    for file, parts in evidence.items():
        if not _overlaps(task_tokens, tokenize(" ".join(parts))):
            findings.append(
                Finding(
                    check=CHECK_NAME,
                    code=CODE,
                    severity="info",
                    file=file,
                    message=f"'{file}' shares no tokens with the task — possibly out of scope",
                    confidence="low",
                )
            )
    return findings


def tokenize(text: str) -> set[str]:
    spaced = _CAMEL_BOUNDARY.sub(r"\1 \2", text)
    return {
        token
        for token in _SPLIT.split(spaced.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }


def _overlaps(task: set[str], file: set[str]) -> bool:
    """Plural-tolerant intersection, so 'import' matches 'imports'."""
    for token in task:
        if token in file or f"{token}s" in file:
            return True
        if token.endswith("s") and token[:-1] in file:
            return True
    return False
