"""Single source of truth for the check and report contracts.

Checks are pure and synchronous: git calls, file reads, and environment
discovery happen once in ``context/`` and arrive here via ``CheckContext``.
If a check "needs" I/O, extend ``CheckContext`` instead.

Wire-facing models serialize to camelCase (``docs/SPEC.md``), so dump them
with ``by_alias=True``. Python-side attribute names stay snake_case.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    """Base for anything that crosses the JSON boundary (CLI --json, API, UI)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- diff model -------------------------------------------------------------

FileStatus = Literal["added", "deleted", "modified", "renamed", "binary"]
LineKind = Literal["add", "del", "context"]


class HunkLine(WireModel):
    """One line inside a hunk, +/-/space prefix stripped."""

    kind: LineKind
    text: str
    old_line: int | None = None
    new_line: int | None = None


class Hunk(WireModel):
    """One contiguous change region. Paths are always git-normalized ('/')."""

    file: str
    old_file: str | None = None
    status: FileStatus
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[HunkLine] = Field(default_factory=list)

    @property
    def range(self) -> str:
        """Human-readable new-side line range, e.g. "12-20"."""
        if self.new_lines == 0:
            return str(self.new_start)
        return f"{self.new_start}-{self.new_start + self.new_lines - 1}"

    def added_lines(self) -> list[HunkLine]:
        return [ln for ln in self.lines if ln.kind == "add"]

    def removed_lines(self) -> list[HunkLine]:
        return [ln for ln in self.lines if ln.kind == "del"]


# --- task -------------------------------------------------------------------

TaskSource = Literal["flag", "taskfile", "transcript", "prompt", "api"]


class TaskInfo(WireModel):
    text: str
    source: TaskSource


# --- findings ---------------------------------------------------------------

Severity = Literal["error", "warn", "info"]
Confidence = Literal["high", "medium", "low"]

DETERMINISTIC_ISSUE_CODES: tuple[str, ...] = (
    "placeholder-code",
    "test-tampering",
    "unresolved-import",
    "scope-drift",
)

# Deliberately curated and intent-first, not a general review taxonomy.
# change-narration and misleading-claim judge claims against the diff, which
# needs a transcript; they are registered now because codes are API.
AGENTIC_ISSUE_CODES: tuple[str, ...] = (
    "request-unfulfilled",
    "unrequested-change",
    "unintended-removal",
    "dead-integration",
    "instruction-file-disobeyed",
    "docs-drift",
    "change-narration",
    "misleading-claim",
)

ALL_ISSUE_CODES: tuple[str, ...] = DETERMINISTIC_ISSUE_CODES + AGENTIC_ISSUE_CODES

CODE_MEANINGS: dict[str, str] = {
    "placeholder-code": 'Stub markers, "rest of ..." hand-waves, TODO/FIXME, empty bodies added',
    "test-tampering": "Test files deleted, skip markers added, assertions gutted",
    "unresolved-import": "Added import doesn't resolve: missing module, uninstalled "
    "package, invented name",
    "scope-drift": "File shares no tokens with the task — possibly out of scope "
    "(heuristic, always info/low)",
    "request-unfulfilled": "The task asked for something the diff doesn't deliver",
    "unrequested-change": "Scope creep — a hunk that doesn't follow from the task",
    "unintended-removal": "Code or behavior removed when the task didn't call for removal",
    "dead-integration": "New code isn't wired in — defined but never called or exported",
    "instruction-file-disobeyed": "The diff violates a project instruction file rule",
    "docs-drift": "The change makes existing docs or comments wrong without updating them",
    "change-narration": "The agent's account of its changes doesn't match the diff (reserved)",
    "misleading-claim": "The agent claimed something the evidence contradicts (reserved)",
}

# Codes the model may emit directly. unrequested-change is derived by vouch
# from unrequested hunk classifications, never model-emitted.
AGENT_EMITTABLE_CODES: tuple[str, ...] = (
    "request-unfulfilled",
    "unintended-removal",
    "dead-integration",
    "instruction-file-disobeyed",
    "docs-drift",
)


class Finding(WireModel):
    """``check`` names the producing module and may change; ``code`` is API.

    severity: error = objectively wrong · warn = a human should look ·
    info = heuristic. Heuristic checks must always report 'low' confidence.
    """

    check: str
    code: str
    severity: Severity
    message: str
    confidence: Confidence
    file: str | None = None
    line: int | None = None


# --- check inputs -----------------------------------------------------------

Classification = Literal["requested", "supporting", "unrequested"]
Verdict = Literal["clean", "review", "fail"]
AgentStatus = Literal["ran", "disabled", "no-api-key", "no-hunks", "error"]


class ProjectAccess(BaseModel):
    """Pre-loaded project state so checks stay I/O-free."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    repo_root: str
    # Post-change contents of diff-touched files, keyed by git-normalized path.
    # Deleted and binary files are absent.
    files: dict[str, str] = Field(default_factory=dict)
    # Importable top-level names in the *target* repo's environment, not vouch's.
    # Empty when no environment could be discovered — the imports check then
    # declines to judge third-party imports rather than flagging every real
    # dependency of the project under review.
    installed_modules: set[str] = Field(default_factory=set)
    env_discovered: bool = False
    # Project-internal modules: dotted name -> git-normalized path. Lets the
    # imports check verify in-repo modules even with no environment at all.
    module_files: dict[str, str] = Field(default_factory=dict)
    # Dotted module name -> its top-level public names, for catching imports
    # of things a real module doesn't actually define.
    module_exports: dict[str, set[str]] = Field(default_factory=dict)
    # Modules that re-export dynamically (star imports, __getattr__), where an
    # absent name proves nothing.
    opaque_modules: set[str] = Field(default_factory=set)
    # Directory -> module names importable as siblings from files in it. Test
    # helpers like conftest and local fixtures are imported this way.
    sibling_modules: dict[str, set[str]] = Field(default_factory=dict)


class CheckContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    hunks: list[Hunk]
    task: TaskInfo
    project: ProjectAccess
