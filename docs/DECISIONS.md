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
- Verdict rule ported verbatim from the TS reference over reinventing thresholds: any `error` → fail, any finding or agent flag → review; keeps exit codes comparable across both implementations.
- Agent findings pinned to `warn` over letting the model pick severity: Pass 2 may demand review but must never claim objective failure.
- camelCase wire keys via pydantic alias generator over snake_case JSON: the React client is a first-class consumer and the schema is frozen at v1.
- `default_factory` for all model defaults over bare instances: a shared `ReportMeta()` froze `createdAt` at import time.
- Hand-rolled diff parser over `unidiff`/GitPython: stdlib-only, and we need git's rename and binary detection anyway.
- Imports check resolves against the *target* repo's discovered environment over vouch's own: resolving against vouch's `site-packages` would flag every real dependency of the project under review.
- Python 3.14 verified for local dev with `requires-python = ">=3.12"` and CI on 3.12/3.13: full stack (pydantic 2.14, FastAPI 0.143, anthropic 1.13) installs clean, so no interpreter pin needed.
- Containerised API mounts the target repo read-only over a writable bind: enforces the never-mutate invariant at the filesystem layer, not just in code.
- Empty-body detection via AST over regex: in Python `pass` and `...` are correct in abstract methods, `@overload`, Protocols, and `.pyi` stubs, and only the AST has the context to tell a stub from a declaration.
- Comment markers matched outside quoted spans over anywhere in the line: a comment that quotes "TODO" is documenting it, not declaring it (dogfooded, `docs/CAUGHT.md`).
- Imports in `try/except ImportError` exempt over flagging every unresolved name: an import allowed to fail is an optional dependency (dogfooded, `docs/CAUGHT.md`).
- Imports check stays silent on third-party names when no environment was discovered over guessing: a false "hallucinated import" on a real dependency would destroy trust in the headline check.
- Python keywords (`import`, `self`, `def`, `return`) treated as scope stopwords: they appear in nearly every Python file, so counting them as signal would rescue every file from scope-drift.
- Assertion counting by net delta over AST body comparison: a refactor that moves assertions nets to zero and stays silent, so only real loss flags.
- Findings sorted by severity then file then line in the pipeline over check-registration order: the terminal report should lead with what matters most.
