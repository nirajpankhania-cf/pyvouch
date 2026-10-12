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
  --save                 Record the run so the workspace UI can open it

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

`severity`: `error` (objectively wrong) · `warn` (a human should look) · `info` (heuristic).
`confidence`: `high` · `medium` · `low`. Heuristic checks always report `low`.

A check that raises is a vouch bug, never a finding: the pipeline surfaces it as a tool error (exit `2`).

## Verdict

| Condition | Verdict | Exit |
|---|---|---|
| Any deterministic finding with `severity: error` | `fail` | 1 |
| Else any deterministic finding at all (incl. `info`), or the agent ran and flagged anything | `review` | 1 |
| Else | `clean` | 0 |

Agent findings are always `warn` — model judgment is never an objective error, so Pass 2 can
raise `review` but never `fail`. `info`-only findings still produce `review`: scope drift alone
is deliberately consequential. Unrequested hunks count toward the verdict directly as well as
through derived findings, so per-code config cannot silently downgrade a run.

## Pass 2 — Agentic

Classify each hunk: `requested` | `supporting` | `unrequested`.

Read-only tools: `read_file`, `read_git_log`, `list_dir`, `search`. Budget ~15 tool calls then force verdict. Surface cost.

Default model: `claude-opus-5-5` (config override). Degrade cleanly with no API key (`--no-agent` behavior).

The pipeline reports why the agent did or didn't run — `ran` · `disabled` · `no-api-key` ·
`no-hunks` · `error` — so the CLI can explain a Pass-1-only result instead of silently
omitting Pass 2. Agent failures degrade; they never crash the run.

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

Keys are camelCase on the wire so the React client reads them idiomatically; Python
attributes stay snake_case and serialize through a pydantic alias generator.

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

## HTTP API (v1)

Default port **8741**. All bodies and responses use the same camelCase wire keys as `--json`.

| Route | Purpose |
|---|---|
| `POST /reviews` | Run the pipeline against the configured repo root. `201` + full report. |
| `GET /reviews` | History, newest first. Summary fields only. |
| `GET /reviews/{id}` | The stored report, byte-identical to what `POST` returned. |
| `GET /health` | Liveness, version, repo root, whether an API key is present. |

`POST /reviews` body: `{ task, mode: working|staged|base, baseRef?, noAgent?, model? }`.
`task` is required and becomes `task.source = "api"` — the API never prompts and never
guesses from a transcript. Response adds `id`, `agentStatus`, `agentError`, `durationMs`
alongside `report`.

`GET /reviews` takes `limit` (1–200, default 50) and `offset`, and returns
`{ runs, total }` where `total` counts all stored runs, not just the page.

Status codes: `400` the repo or ref is unusable (not a git repo, bad base ref) ·
`422` the request body is invalid · `404` unknown run id · `500` a check crashed.
The repo root comes from `VOUCH_REPO_ROOT` (the container mounts it read-only).

Auth: none for local v1. GitHub OAuth/App only if a later phase adds remote PR fetch.

## Persistence

Runs live in one SQLite table; the report is stored as the JSON document above, with
verdict/counts/branch/commit duplicated into columns so the history list never has to
parse a report. Location: `VOUCH_DB`, else `$XDG_DATA_HOME/vouch/runs.db`.

The API records every completed review. The CLI records only when asked (`--save`) or
configured (`VOUCH_DB` set), so a CI run does not accumulate state nobody reads. A store
that cannot be written warns and leaves the exit code alone — persistence must never
change a verdict.

## Environment

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Enables Pass 2. Absent → `no-api-key`, Pass 1 still reports. |
| `VOUCH_REPO_ROOT` | The repo the API reviews. Default: server working directory. |
| `VOUCH_DB` | Run store location; also makes the CLI record without `--save`. |
| `VOUCH_ENV_FILE` | Read this file instead of `./.env`. |

Both the CLI and the API load `./.env` on startup (see `.env.example`). A real environment
variable always wins over the file, including one exported as empty. Parsing is deliberately
minimal — `KEY=value`, optional `export`, optional quotes, `#` comments — not a full dotenv.

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
