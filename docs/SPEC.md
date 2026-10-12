# vouch (Python) — Specification

> Intent-aware verification for AI-generated Python. You tell it what you asked for; it tells you what you actually got.

Inspired by the TypeScript `vouch-cli` design. This SPEC is for the **Python full-stack** project (core + CLI + API + UI).

## Problem

Code review asks “is this good code?” Agent diffs need “is this the code I asked for?” Characteristic failures: scope creep, gutted tests, hallucinated imports, stubs, unrelated files.

## Surfaces

| Surface | Role |
|---|---|
| CLI `vouch` | Primary / CI. Works with no UI. |
| FastAPI | `POST /reviews`, `GET /reviews`, `GET /reviews/{id}` (+ health). |
| React UI | Request a review, inspect structured results, browse history. |

All surfaces share one core pipeline and one result schema.

## CLI (v1)

```
vouch check [options]
  -m, --message <task>   Task text (highest priority)
  --staged               Staged changes only
  --base <ref>           Diff vs merge-base with ref (PR-style)
  --json                 Machine-readable report
  --no-agent             Pass 1 only
  --model <id>           Override agent model (default claude-opus-5-5)

vouch init               Write config + gitignore TASK.md
vouch list-codes         Print issue codes
```

Exit codes (API): `0` clean · `1` findings · `2` tool error.

## Task resolution

1. `-m` / API-body `task`
2. `TASK.md` (gitignored scratch)
3. Transcript loader (best-effort; Claude Code JSONL under `~/.claude/projects/<encoded-cwd>/` first)
4. Interactive prompt (TTY CLI only)

If a transcript task is used: always echo it; confirm on TTY; never silent.

## Pass 1 — Static (zero tokens)

Pure functions on diff + task + project access.

| Check | Code | Catches | Python approach |
|---|---|---|---|
| placeholders | `placeholder-code` | TODO/FIXME, “not implemented”, empty bodies | Regex on added lines + light `ast` where reliable |
| tests | `test-tampering` | Deleted tests, skip/only, assertion loss | Path conventions (`tests/`, `test_*.py`, `*_test.py`); pytest markers; assert-count delta |
| imports | `unresolved-import` | Missing modules / invented names | `ast` + resolve against project + installed dists |
| scope | `scope-drift` | Files with no token overlap vs task | Heuristic; severity info; confidence low |

Finding fields: `check`, `code`, `severity`, `file`, `line?`, `message`, `confidence`.

## Pass 2 — Agentic

Classify each hunk: `requested` | `supporting` | `unrequested`.

Read-only tools: `read_file`, `read_git_log`, `list_dir`, `search`. Budget ~15 tool calls then force verdict. Surface cost.

Default model: `claude-opus-5-5` (config override). Degrade cleanly with no API key (`--no-agent` behavior).

### Agentic issue codes

| Code | Meaning |
|---|---|
| `request-unfulfilled` | Task not delivered |
| `unrequested-change` | Derived from `unrequested` hunks |
| `unintended-removal` | Removals the task didn’t ask for |
| `dead-integration` | Defined but not wired |
| `instruction-file-disobeyed` | Violates CLAUDE.md / AGENTS.md / etc. |
| `docs-drift` | Docs/comments now wrong |
| `change-narration` | Reserved |
| `misleading-claim` | Reserved |

Anything mechanical belongs in Pass 1, not the LLM.

## JSON report (v1)

Versioning: additive → same version; breaking → bump.

```jsonc
{
  "version": 1,
  "task": { "text": "...", "source": "flag|taskfile|transcript|prompt|api" },
  "deterministic": [ /* Finding */ ],
  "agent": {
    "ran": true,
    "hunks": [
      {
        "file": "...",
        "range": "...",
        "classification": "requested|supporting|unrequested",
        "reason": "..."
      }
    ],
    "findings": [ /* Finding; check always "agent" */ ],
    "summary": "...",
    "cost": { "inputTokens": 0, "outputTokens": 0, "toolCalls": 0 }
  },
  "verdict": "clean|review|fail",
  "meta": {
    "createdAt": "ISO-8601",
    "commit": "sha?",
    "branch": "?",
    "repoRoot": "?"
  }
}
```

## HTTP API (v1 sketch)

- `POST /reviews` — body: `{ task, mode: working|staged|base, baseRef?, noAgent?, model? }` relative to a configured repo root (local v1). Returns full report + `id`.
- `GET /reviews` — newest first; summary fields for the list.
- `GET /reviews/{id}` — full report.
- `GET /health` — liveness.

Auth: none for local v1. GitHub OAuth/App only if a later phase adds remote PR fetch.

## Persistence

Each completed pipeline writes a run (SQLite). UI lists newest-first; opening a run shows the same inspection layout as a fresh result.

## UI inspection layout

One screen = one run:

- Left: history list  
- Center: files / hunks with classification tags  
- Right: findings by code, navigate to location  
- Header: task, verdict, pass toggles, cost  

Not a raw JSON viewer (provide Copy JSON).

## Non-goals (v1)

- Auto-fix
- Quality-only review
- Multi-tenant cloud
- Non-Python AST depth (diff-level ok)
- Perfect transcript compatibility
