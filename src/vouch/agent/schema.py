"""The structured verdict the model must return.

Validated with pydantic in ``loop.py``; this is the single source of truth for
the shape that ``core/report.py`` then serializes.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from vouch.core.types import (
    AGENTIC_ISSUE_CODES,
    Classification,
    Confidence,
    WireModel,
)


class HunkVerdict(WireModel):
    file: str
    # Human-readable line range, echoed back from the prompt.
    range: str
    classification: Classification
    # One sentence on why — especially required for 'unrequested'.
    reason: str = ""


class ModelFinding(WireModel):
    """One issue-coded finding from the model.

    ``file``/``line`` are optional: codes about absence, like
    ``request-unfulfilled``, may have no location.
    """

    code: str
    message: str
    confidence: Confidence = "medium"
    file: str | None = None
    line: int | None = None

    @field_validator("code")
    @classmethod
    def _known_code(cls, value: str) -> str:
        # The full taxonomy, not just the emittable subset, so a valid but
        # non-emittable code degrades to a dropped finding rather than a
        # failed parse of the whole verdict.
        if value not in AGENTIC_ISSUE_CODES:
            raise ValueError(f"unknown issue code '{value}'")
        return value

    @field_validator("line")
    @classmethod
    def _positive_line(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            return None
        return value


class AgentVerdict(WireModel):
    hunks: list[HunkVerdict] = Field(default_factory=list)
    # Defaulted so classification-only output, and the budget-exhausted path,
    # still parse.
    findings: list[ModelFinding] = Field(default_factory=list)
    summary: str = ""
