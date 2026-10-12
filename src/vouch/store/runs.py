"""Run persistence on stdlib sqlite3.

The report is already a versioned JSON document, so it is stored whole and
the columns alongside it exist only to serve the list view without parsing
every row. No ORM: there is one table and one query shape.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from vouch.core.report import VouchReport, WireModel
from vouch.core.types import Verdict

DEFAULT_DB_NAME = "runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    task_text     TEXT NOT NULL,
    task_source   TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    agent_ran     INTEGER NOT NULL,
    finding_count INTEGER NOT NULL,
    error_count   INTEGER NOT NULL,
    repo_root     TEXT,
    branch        TEXT,
    commit_sha    TEXT,
    duration_ms   REAL,
    report_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at DESC);
"""


class RunSummary(WireModel):
    """What the history list needs — never the whole report."""

    id: str
    created_at: str
    task_text: str
    task_source: str
    verdict: Verdict
    agent_ran: bool
    finding_count: int
    error_count: int
    branch: str | None = None
    commit: str | None = None
    duration_ms: float | None = None


def default_db_path() -> Path:
    """Where runs live unless overridden by ``VOUCH_DB``."""
    override = os.environ.get("VOUCH_DB")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "vouch" / DEFAULT_DB_NAME


class RunStore:
    """A single-table SQLite store. Safe to construct per request."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self._lock = threading.Lock()
        self._shared: sqlite3.Connection | None = None

        if str(self.path) == ":memory:":
            # An in-memory database vanishes with its connection, so one is
            # held open for the store's lifetime.
            self._shared = self._connect()
            with self._shared:
                self._shared.executescript(_SCHEMA)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._cursor() as cursor:
                cursor.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # check_same_thread=False because the API runs sync handlers in a
        # threadpool; the lock below is what actually serializes access.
        connection = sqlite3.connect(str(self.path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _cursor(self):
        if self._shared is not None:
            with self._lock, self._shared:
                cursor = self._shared.cursor()
                try:
                    yield cursor
                finally:
                    cursor.close()
            return
        # A connection per operation is already thread-safe, and SQLite's own
        # locking handles concurrent writers to the file.
        with closing(self._connect()) as connection, connection:
            yield connection.cursor()

    def save(self, report: VouchReport, duration_ms: float | None = None) -> str:
        run_id = uuid.uuid4().hex
        findings = list(report.deterministic) + list(report.agent.findings or [])
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO runs (
                    id, created_at, task_text, task_source, verdict, agent_ran,
                    finding_count, error_count, repo_root, branch, commit_sha,
                    duration_ms, report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    report.meta.created_at,
                    report.task.text,
                    report.task.source,
                    report.verdict,
                    1 if report.agent.ran else 0,
                    len(findings),
                    sum(1 for f in findings if f.severity == "error"),
                    report.meta.repo_root,
                    report.meta.branch,
                    report.meta.commit,
                    duration_ms,
                    json.dumps(report.to_json_dict()),
                ),
            )
        return run_id

    def list(self, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        """Newest first."""
        with self._cursor() as cursor:
            rows = cursor.execute(
                """
                SELECT id, created_at, task_text, task_source, verdict, agent_ran,
                       finding_count, error_count, branch, commit_sha, duration_ms
                FROM runs
                ORDER BY created_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [_summary_of(row) for row in rows]

    def get(self, run_id: str) -> dict | None:
        """The full stored report, or None if the id is unknown."""
        with self._cursor() as cursor:
            row = cursor.execute(
                "SELECT report_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["report_json"])

    def count(self) -> int:
        with self._cursor() as cursor:
            return int(cursor.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"])


def _summary_of(row: sqlite3.Row) -> RunSummary:
    return RunSummary(
        id=row["id"],
        created_at=row["created_at"],
        task_text=row["task_text"],
        task_source=row["task_source"],
        verdict=row["verdict"],
        agent_ran=bool(row["agent_ran"]),
        finding_count=row["finding_count"],
        error_count=row["error_count"],
        branch=row["branch"],
        commit=row["commit_sha"],
        duration_ms=row["duration_ms"],
    )
