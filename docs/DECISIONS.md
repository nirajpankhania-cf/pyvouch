# Decisions

One line per significant technical decision: `choice over alternative: reason`.

- Python core + FastAPI + React over TS monolith port: engine must own Python AST; UI is a separate surface on a stable JSON contract.
- CLI-first with optional workspace UI over UI-required product: CI and local hooks need a headless binary; history/inspection should not gate verification.
- SQLite run store over “only stdout”: enables UI history and reopen without inventing a cloud.
- Shared pydantic models for CLI JSON and API over duplicate TypeScript types as source of truth: generate or mirror TS types from OpenAPI if needed; Python owns the schema.
- `ast` + import resolution over regex-only imports: catches hallucinated modules with acceptable complexity for v1.
- Scope as low-confidence heuristic over LLM-in-pass-1: keep Pass 1 free/fast; intent stays Pass 2.
- Default agent model `claude-opus-5-5` over smaller defaults: intent classification is the product’s hard semantic job; make override easy for cost.
- Calm CLAUDE.md / AGENTS.md (no CRITICAL spam) for Opus 5.5: stronger models overtrigger on aggressive harness language.
- Local git diffs require no GitHub auth over GitHub-centric design: auth only when a later phase fetches remote PRs.
