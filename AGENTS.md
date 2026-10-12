# AGENTS.md — vouch (Python)

Short, tool-neutral project guide. Full detail: `docs/SPEC.md`, `docs/PLAN.md`.

## What this is

Full-stack **intent-aware verification** for AI-written Python:

- Pass 1: static checks (no LLM)
- Pass 2: agentic hunk classification against the user’s task
- Surfaces: **CLI** (CI/primary) + **FastAPI** + **React** workspace (request / inspect / history)

Inspiration: [nirajpankhania/vouch](https://github.com/nirajpankhania/vouch). Implementation language for the engine: **Python only**.

## Defaults for coding agents

- Model preference when choosing: **Claude Opus 5.5** (`claude-opus-5-5`).
- Keep changes scoped to the asked phase/task; ask before large refactors.
- Verify with `pytest` and `ruff check .` before claiming done.
- Do not reimplement core checks in the frontend.
- Do not mutate the analyzed repo from the verifier agent loop.

## Commands

```bash
uv sync
pytest
ruff check .
uvicorn vouch.api.app:app --reload --port 8741
vouch check -m "task text" [--no-agent] [--json]
cd frontend && npm run dev
```

## Where to edit

| Concern | Path |
|---|---|
| Pipeline / types / verdict | `src/vouch/core/` |
| Static checks | `src/vouch/checks/` |
| Agentic pass | `src/vouch/agent/` |
| Diff/task/transcripts | `src/vouch/context/` |
| Persistence | `src/vouch/store/` |
| HTTP API | `src/vouch/api/` |
| CLI | `src/vouch/cli.py` |
| UI | `frontend/` |

## Contracts

- Exit codes: `0` clean · `1` findings · `2` tool error
- Issue codes and JSON schema: `docs/SPEC.md` (stable API)
- Decision log: append to `docs/DECISIONS.md`

## Non-goals

Quality-only review, auto-fix, cloud SaaS-first, TS verification core.
