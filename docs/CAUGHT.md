# Caught while building vouch

Real findings from running `vouch check` on vouch's own diffs. The dogfood log:
if the tool can't find anything in its author's work, it won't find anything in yours.

Format: `date · code · what it caught · what we did`.

---

## 2026-10-12 · `unresolved-import` · a module that didn't exist yet

First ever run of `vouch check` on its own working tree, during Phase 2:

```
error unresolved-import     src/vouch/core/pipeline.py:119
      module not found in this repo: from vouch.agent.loop import run_agent
```

True positive. `pipeline.py` imported the Phase 4 agent loop, which hadn't been written.
The import was deliberately optional — wrapped in `try/except ImportError` so Pass 2
degrades when the agent isn't available — but the check didn't know that.

**Fix:** imports inside a `try` that handles `ImportError`, `ModuleNotFoundError`, or a
bare `except` are now exempt. An import allowed to fail is an optional dependency, not a
hallucination. Caught a real design gap: without the exemption, every optional-dependency
pattern in Python would have been a false positive.

## 2026-10-12 · `placeholder-code` · the check flagged its own documentation

Same run:

```
warn  placeholder-code      src/vouch/checks/placeholders.py:39
      explicit "not implemented" comment: "# A '#' comment is required for the prose
      idioms: "not implemented" appears in"
```

False positive, and a nicely self-referential one: the comment explaining the
"not implemented" pattern contains the phrase it documents.

**Fix:** marker matching now ignores occurrences inside quotes within a comment, and
comment extraction properly skips `#` characters inside string literals. A comment that
*quotes* a marker is discussing the idiom, not declaring a stub. The same class of bug
hit the TS implementation (prose and `.diff` fixtures needed a file-type gate), which
suggests "the checker trips over text about checking" is a recurring hazard worth a test
each time.

## 2026-10-12 · `dead-integration` (by hand) · an unused test helper

Not a tool catch — spotted while reviewing before commit. `tests/test_cli.py` defined a
`check_in()` helper that nothing called, with a bogus `env=` argument that would not have
worked. Ruff doesn't flag unused module-level functions, which is exactly the gap the
agentic `dead-integration` code is meant to cover once Pass 2 lands.

**Fix:** deleted. Logged here as a reminder that Pass 1 cannot see this class of problem.
