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

## 2026-10-12 · `unresolved-import` · sibling imports in the test directory

Running `vouch check` on the Phase 4 diff:

```
error unresolved-import     tests/test_agent_loop.py:7
      'conftest' is not installed in this project's environment: from conftest import make_context
error unresolved-import     tests/test_agent_loop.py:8
      'fakes' is not installed in this project's environment: from fakes import FakeClient, ...
error unresolved-import     tests/test_pipeline.py:10
      'fakes' is not installed in this project's environment: from fakes import FakeClient, ...
```

False positive, three times over. The module index treated only the repo root, `src/`, and
`lib/` as source roots, so `tests/conftest.py` and `tests/fakes.py` were invisible — yet
both imports work, because Python puts a script's own directory on `sys.path` and pytest
does the same for test directories.

**Fix:** `ProjectAccess` now carries `sibling_modules`, mapping each directory to the
modules importable from inside it, and the imports check consults it before reporting a
third-party miss. Worth noting the check was *right* that these aren't installed packages
and aren't in the module index; it was the resolution model that was incomplete.

## 2026-10-12 · `scope-drift` · caught its own author doing scope creep

After fixing the above and re-running with the same task text — "add the Phase 4 agentic
pass: read-only jailed tools, hand-written agent loop with budget and retry, pydantic
verdict schema, prompts" — the only remaining findings were:

```
info  scope-drift   src/vouch/checks/imports.py
info  scope-drift   src/vouch/context/project.py
info  scope-drift   src/vouch/core/types.py
info  scope-drift   tests/test_imports.py
```

True positive. Those four files are the sibling-module fix, which has nothing to do with
adding the agentic pass. The heuristic correctly noticed that a chunk of the diff didn't
follow from the stated task — the exact failure mode the product exists to catch, found in
its author's own commit. Kept as-is rather than split, but the finding was right to ask.

## 2026-10-12 · `dead-integration` (by hand) · an unused test helper

Not a tool catch — spotted while reviewing before commit. `tests/test_cli.py` defined a
`check_in()` helper that nothing called, with a bogus `env=` argument that would not have
worked. Ruff doesn't flag unused module-level functions, which is exactly the gap the
agentic `dead-integration` code is meant to cover once Pass 2 lands.

**Fix:** deleted. Logged here as a reminder that Pass 1 cannot see this class of problem.
