"""FastAPI app. Local v1 has no auth; the workspace UI is the only client.

Handlers stay thin: validate input, call the core pipeline or the store,
return the shared report schema. No check logic lives here.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from vouch import __version__
from vouch.api.schemas import (
    HealthResponse,
    PromptItem,
    PromptListResponse,
    ReviewListResponse,
    ReviewRequest,
    ReviewResponse,
)
from vouch.context.diff import DiffError, DiffMode
from vouch.context.gitcmd import GitError
from vouch.context.task import TaskError
from vouch.context.transcripts import MAX_PROMPTS, recent_prompts
from vouch.core.pipeline import CheckCrashError, PipelineOptions, run_pipeline
from vouch.core.types import TaskInfo
from vouch.envfile import load_env_file
from vouch.store.runs import RunStore, default_db_path

# The repo under review. In the container this is the read-only mount.
REPO_ROOT_ENV = "VOUCH_REPO_ROOT"
# The built frontend. Absent in dev, where Vite serves it instead.
UI_DIR_ENV = "VOUCH_UI_DIR"

# Before the app exists, so /health reports key availability accurately.
load_env_file()

app = FastAPI(
    title="vouch",
    version=__version__,
    summary="Intent-aware verification for AI-written Python.",
)

# The Vite dev server is the only browser client in local v1.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8742",
        "http://127.0.0.1:8742",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def repo_root() -> str:
    return os.environ.get(REPO_ROOT_ENV) or str(Path.cwd())


@lru_cache(maxsize=4)
def _store_for(path: str) -> RunStore:
    return RunStore(path)


def get_store() -> RunStore:
    """Store dependency. Tests replace this via ``app.dependency_overrides``."""
    return _store_for(str(default_db_path()))


Store = Annotated[RunStore, Depends(get_store)]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        repo_root=repo_root(),
        agent_available=bool(os.environ.get("ANTHROPIC_API_KEY")),
    )


@app.get("/prompts", response_model=PromptListResponse)
def list_prompts(limit: int = Query(default=MAX_PROMPTS, ge=1, le=50)) -> PromptListResponse:
    """Recent prompts typed to a coding agent in the repo under review.

    Offered rather than applied: the UI shows them so the task can be picked
    instead of retyped, but the user still chooses which one.
    """
    found = recent_prompts(repo_root(), limit=limit)
    return PromptListResponse(
        prompts=[
            PromptItem(text=p.text, summary=p.summary, tool=p.tool, session=p.session)
            for p in found
        ]
    )


@app.post("/reviews", response_model=ReviewResponse, status_code=201)
def create_review(body: ReviewRequest, store: Store) -> ReviewResponse:
    if body.mode == "base" and not body.base_ref:
        raise HTTPException(status_code=422, detail="baseRef is required when mode is 'base'")

    mode = {
        "staged": DiffMode.staged,
        "working": DiffMode.working,
    }.get(body.mode)
    diff_mode = DiffMode.base(body.base_ref or "") if body.mode == "base" else mode()

    try:
        result = run_pipeline(
            PipelineOptions(
                mode=diff_mode,
                cwd=repo_root(),
                task=TaskInfo(text=body.task, source="api"),
                agent=not body.no_agent,
                model=body.model,
                prompt=False,
            )
        )
    except (DiffError, TaskError, GitError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckCrashError as exc:
        # A check bug is ours, not the caller's.
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    run_id = store.save(result.report, result.duration_ms)
    return ReviewResponse(
        id=run_id,
        report=result.report.to_json_dict(),
        agent_status=result.agent_status,
        agent_error=result.agent_error,
        duration_ms=result.duration_ms,
    )


@app.get("/reviews", response_model=ReviewListResponse)
def list_reviews(
    store: Store,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ReviewListResponse:
    return ReviewListResponse(runs=store.list(limit=limit, offset=offset), total=store.count())


@app.get("/reviews/{run_id}")
def get_review(run_id: str, store: Store) -> dict:
    report = store.get(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"no review with id '{run_id}'")
    return report


def ui_directory() -> Path | None:
    """The built workspace, if there is one. In dev the Vite server serves it."""
    override = os.environ.get(UI_DIR_ENV)
    root = Path(__file__).resolve().parents[3]
    candidate = Path(override) if override else root / "frontend" / "dist"
    return candidate if (candidate / "index.html").is_file() else None


def mount_ui(target: FastAPI) -> bool:
    """Serve the built workspace at ``/``. Returns whether there was one.

    Called after the API routes are declared so they still win. ``html=True``
    serves index.html for ``/`` itself; there is no catch-all fallback, since
    the workspace is one screen and a 404 on an asset should stay a 404.
    """
    directory = ui_directory()
    if directory is None:
        return False
    target.mount("/", StaticFiles(directory=str(directory), html=True), name="ui")
    return True


mount_ui(app)
