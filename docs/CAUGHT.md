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

## 2026-10-12 · nothing · a clean run, and the bug it couldn't have found

The Phase 5 diff (run store, three `/reviews` endpoints, CLI `--save`) ran **clean**:
no placeholders, no missing imports, no gutted tests, and enough task overlap across
`store/`, `api/`, `cli.py`, SPEC and PLAN that scope held.

Honest counterpoint: that diff shipped with a real defect, and Pass 1 was never going to
see it. `RunStore` held one long-lived SQLite connection, which FastAPI promptly used
from a threadpool worker:

```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that
same thread.
```

Sixteen API tests failed. The tests caught it; vouch structurally cannot — it's a runtime
concurrency property, not an intent mismatch, and "does this diff match the task?" has
nothing to say about it. Worth writing down precisely because a clean verdict is easy to
over-read: `clean` means *no evidence of the five failure modes*, not *correct*.

## 2026-10-12 · `scope-drift` · a build artifact that should never have been tracked

Dogfooding the Phase 6 diff:

```
info  scope-drift   frontend/index.html
info  scope-drift   frontend/tsconfig.tsbuildinfo
```

One true positive, one false. `tsconfig.tsbuildinfo` is a TypeScript incremental-build
cache that `.gitignore` didn't cover — it would have been committed, and would have
churned on every build thereafter. Fixed by ignoring `*.tsbuildinfo`.

`frontend/index.html` is the false positive, and an instructive one: it's twelve lines of
unavoidable scaffolding whose only words are `html`, `head`, `body` and `root`, so it
shares no tokens with any task anyone would write. The heuristic has no way to know that.
It reports `info`/`low` for exactly this reason — scope drift is a question, not a verdict.

Worth noting the shape of the win: the finding that mattered wasn't about intent at all.
A file-level "this doesn't look related to your task" sweep catches tracked junk as a side
effect, because build artifacts are never related to the task.

## 2026-10-12 · a real API key sent to the model, by vouch itself

Not a finding — the opposite. The first live Pass 2 run (a scratch repo, a deliberately
sloppy diff) came back with this classification:

```
warn  unrequested-change  .env:1
      This adds a .env config file containing what looks like a real Anthropic
      API key, which has nothing to do with changing the greeting in app.py.
```

The judgement is correct. The problem is how it was reached: the model could describe the
key because the key was in the prompt. `get_hunks` synthesizes untracked files as added
hunks — deliberately, since a file an agent just wrote is exactly what vouch exists to
inspect — and that scratch repo had no `.gitignore`, so the `.env` became a hunk and its
contents were rendered verbatim into the request.

A tool whose entire job is sending your diff to a model has no business shipping your
credentials with it.

**Fix:** `vouch.agent.secrets.holds_secrets` gates three paths out, not one. Prompts render
such hunks as a path and a line count; `read_file` refuses them; `search` drops matching
lines. Re-running gave the same verdict with a reason that no longer quotes anything:
*"nothing in it calls for adding a credentials file."* Still classified, still `unrequested`,
no secret in the payload.

Two things worth keeping: the gate is by filename, which is a floor and not detection; and
the hunk is withheld rather than dropped, because an unrequested `.env` is a finding worth
having.

## 2026-10-12 · `[1-5]` · the model echoed the prompt's own punctuation

Found by running the live agent against a demo repo rather than a fake client. The verdict
came back correct in substance but wrong in shape:

```
Unrequested hunks
  tests/test_cart.py:[1-5] — Imports pytest only so a cart test can be skipped…
```

Hunks are headed `### file [1-5] (added)` in the prompt, and the model echoed the brackets
into its `range` field. Two consequences, neither visible in that line:

- `_range_start("[1-5]")` can't parse a number, so every derived `unrequested-change`
  finding lost its line number.
- `(file, range)` no longer matches anything in `changes`, so the workspace UI would have
  silently shown those hunks with no classification tag. `_reconcile` only kept them at
  all because it falls back to matching on file alone.

**Fix:** normalize the range on parse, stop inviting the mistake by rendering `range=1-5`
instead of `[1-5]`, and say "no brackets" in the schema instruction. Three layers, because
the normalizer is the only one that survives a model that ignores instructions.

The lesson is about test strategy, not parsing. Every agent test used a scripted fake that
echoed `hunk_range_label()` perfectly, so the bug was unreachable offline — the fakes
encoded my assumption that the model would do the obvious thing. Live runs are not
optional for an LLM-shaped boundary.
---

## It caught scope creep in the change that added the demo seeder

Running vouch on the diff that built `scripts/seed_demo.py` and the transcript loader:

```
Pass 2 — intent
  warn  unrequested-change         docs/PLAN.md:68
        Ticking off `vouch list-codes` and the README as done in Phase 7 has
        nothing to do with transcripts or seeding history; only the seed_demo
        line relates to the task.
```

It is right. While editing `docs/PLAN.md` to tick the Phase 3 transcript items, I also
ticked two Phase 7 boxes that were finished earlier and never marked. The statements are
true, but they are not what the task asked for, and a reviewer reading this diff for the
transcript loader would have had to work out why the plan's Phase 7 moved.

This is the whole product thesis in one finding: nothing here is a bug, no linter would
say a word, and `git diff` shows it without comment. Only something holding the task can
tell you it does not belong.

## A false positive worth keeping: TODOs in demo fixtures

The same run:

```
  warn  placeholder-code           scripts/seed_demo.py:207
        TODO/FIXME comment added: "# TODO: implement once the cache service lands"
```

The seeder's scenarios are Python source held in string literals — a stub with a TODO is
*deliberately* one of the diffs the demo reviews. The check sees an added line containing
a TODO marker and cannot tell data from code.

Not fixed, on purpose. Ignoring markers inside string literals would also silence a TODO
in a docstring, which is a real one, and the only file in the repo that trips this is the
fixture generator. A `warn` the author can read and dismiss is a better trade than a
blind spot, and `placeholders.py` already carries the exemptions that are worth the
complexity (abstract methods, `Protocol` bodies, `.pyi` stubs, quoted markers in prose).

---

## A route that only worked on one port

Not caught by vouch, caught while writing the README section that explains 8741 versus
8742. `GET /prompts` was added to the API but not to the Vite dev-server proxy, so on 8742
the request fell through to Vite, which answered with `index.html`. The UI's fetch failed
to parse that, the error was (deliberately) swallowed because the picker is optional, and
the picker simply never appeared — on one of the two ports, with no error anywhere.

**Fix:** add the route, and a pytest that every OpenAPI path prefix appears in
`vite.config.ts`, so the next new route fails CI instead of failing quietly in a browser.
Verified the test fails without the fix.
