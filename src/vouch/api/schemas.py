"""Request and response shapes for the HTTP surface.

Reuses the same pydantic report models as the CLI's ``--json`` output, so
there is one schema, not two.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from vouch.core.types import AgentStatus, WireModel
from vouch.store.runs import RunSummary

DiffModeName = Literal["working", "staged", "base"]


class ReviewRequest(WireModel):
    task: str = Field(min_length=1, description="What you asked the agent to do.")
    mode: DiffModeName = "working"
    base_ref: str | None = Field(default=None, description="Required when mode is 'base'.")
    no_agent: bool = False
    model: str | None = None


class ReviewResponse(WireModel):
    """The full report plus the id it was stored under."""

    id: str
    report: dict
    agent_status: AgentStatus
    agent_error: str | None = None
    duration_ms: float


class ReviewListResponse(WireModel):
    runs: list[RunSummary]
    total: int


class HealthResponse(WireModel):
    status: str
    version: str
    repo_root: str | None = None
    agent_available: bool = False
