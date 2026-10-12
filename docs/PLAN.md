# Build plan — vouch (Python)

One phase ≈ focused session(s). End each phase with: tests green, decision notes if needed, checkbox update here.

## Phase 0 — Scaffold

- [x] `venv` + pip Python package `vouch`, ruff, pytest, pyproject scripts
- [x] Typer CLI skeleton: `vouch check --help`, `vouch list-codes`, `vouch version`
- [x] FastAPI hello `/health`
- [ ] Vite React app shell in `frontend/` — deferred to Phase 6
- [x] CI: lint + test (3.12 / 3.13)
- [x] Agent docs in place (`AGENTS.md`, PLAN/SPEC/DECISIONS; `CLAUDE.md` is local-only)
- [x] Dockerfile + compose, target repo mounted read-only
- [x] Shared types, report schema, verdict rule (pulled forward from Phase 1)

## Phase 1 — Context layer (no LLM)

- [x] Diff modes: working tree, `--staged`, `--base` (three-dot / merge-base semantics)
- [x] Hunk model + fixtures from real git diffs (10 fixtures incl. rename, binary, mode-only, no-newline, spaces-in-path)
- [x] Hand-rolled unified diff parser; untracked files synthesized as `added`
- [x] Task resolution: `-m` → `TASK.md` → transcript hook → prompt
- [x] Shared types: `Finding`, `CheckContext`, `ProjectAccess`, verdict enum
- [x] Target-environment discovery for imports (venv → declared deps → `env_discovered=False`)

## Phase 2 — Pass 1 static checks (Python)

- [x] `placeholders` (comment idioms outside quotes + AST empty-body detection with abstract/Protocol/`.pyi` exemptions)
- [x] `tests` (pytest + unittest paths, skip/skipif/xfail markers, assertion delta)
- [x] `imports` (`ast` + resolution; relative, project-internal, third-party; `try/except ImportError` exempt)
- [x] `scope` (token overlap; infra denylist; always info/low)
- [x] Terminal + JSON reporters; exit codes
- [x] Milestone: `vouch check --no-agent` useful on a Python repo
- [x] Start `docs/CAUGHT.md` dogfood log

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
