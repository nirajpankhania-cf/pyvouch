# Build plan — vouch (Python)

One phase ≈ focused session(s). End each phase with: tests green, decision notes if needed, checkbox update here.

## Phase 0 — Scaffold

- [ ] `uv` (or poetry) Python package `vouch`, ruff, pytest, pyproject scripts
- [ ] Typer CLI skeleton: `vouch check --help`
- [ ] FastAPI hello `/health`
- [ ] Vite React app shell in `frontend/`
- [ ] CI: lint + test
- [ ] Copy agent docs (`CLAUDE.md`, `AGENTS.md`, this PLAN/SPEC)

## Phase 1 — Context layer (no LLM)

- [ ] Diff modes: working tree, `--staged`, `--base` (three-dot / merge-base semantics)
- [ ] Hunk model + fixtures from real git diffs
- [ ] Task resolution: `-m` → `TASK.md` → prompt
- [ ] Shared types: `Finding`, `Check`, `CheckContext`, verdict enum

## Phase 2 — Pass 1 static checks (Python)

- [ ] `placeholders` (added-line patterns + careful empty-body detection)
- [ ] `tests` (pytest paths, skip markers, assertion delta)
- [ ] `imports` (`ast` + resolution; project + installed)
- [ ] `scope` (token overlap; infra denylist; low confidence)
- [ ] Terminal + JSON reporters; exit codes
- [ ] Milestone: `vouch check --no-agent` useful on a Python repo
- [ ] Start `docs/CAUGHT.md` dogfood log

## Phase 3 — Transcript task (optional convenience)

- [ ] Claude Code JSONL loader (best-effort, never throw)
- [ ] Confirm/echo UX; non-TTY safe behavior
- [ ] Leave hooks for Cursor/Codex loaders later

## Phase 4 — Pass 2 agentic

- [ ] Read-only tools + jail
- [ ] Hand-rolled loop, budget, pydantic-validated verdict
- [ ] Classification rubric + issue codes
- [ ] Cost in report; no-key degradation
- [ ] Default model `claude-opus-5-5`

## Phase 5 — Store + API

- [ ] SQLite run store
- [ ] `POST /reviews`, `GET /reviews`, `GET /reviews/{id}`
- [ ] CLI writes the same store when configured
- [ ] OpenAPI matches SPEC schemas

## Phase 6 — React workspace UI

- [ ] Request review form (task, mode, no-agent)
- [ ] Inspection layout: history | hunks | findings
- [ ] Reopen historical runs
- [ ] Copy JSON; verdict/cost header
- [ ] Full-stack happy path demoable without terminal

## Phase 7 — Polish

- [ ] `vouch init` + config file
- [ ] `vouch list-codes`
- [ ] README: problem → demo → install → architecture
- [ ] Ignore globs; per-check toggles
- [ ] Friendly errors (not-a-repo, bad base)

## Later (do not start early)

- GitHub Action / SARIF
- PR fetch via GitHub App
- Multi-provider LLM
- Compare / watch modes
- Multi-language AST beyond Python

## Session log

<!-- date · phase · shipped · notes -->
