# vouch

**Intent-aware verification for AI-written Python.** You state the task; vouch tells you
whether the diff matches it.

Code review asks *"is this good code?"* An agent's diff needs a different question:
*"is this the code I asked for?"* Those are not the same, and linters answer neither.
The characteristic failures of AI-written code are scope creep, gutted tests,
hallucinated imports, stubs left behind, and the thing you asked for quietly not
happening. vouch looks for exactly those.

It is **not** a linter, a formatter, a type checker, or a code-quality reviewer. Style,
performance and elegance are deliberately out of scope. Run ruff and mypy as well; they
answer a different question.

```
$ vouch check -m "add retry with exponential backoff to PricingClient.fetch_price"

Pass 1 — static
  error unresolved-import          src/shop/client.py:6
        module not found in this repo: from shop.backoff import exponential_delay
  warn  test-tampering             tests/test_cart.py:11
        @pytest.mark.skip added (test disabled)

Pass 2 — intent
  warn  request-unfulfilled        src/shop/client.py:6
        ...nothing in the repo defines exponential_delay, so the backoff isn't
        actually delivered.

FAIL  1 error, 8 warn, 1 info
```

Full logged runs are in [Demo](#demo) below.

---

## Contents

- [How it works](#how-it-works) — the two passes
- [Setup](#setup) — install and run
- [Usage](#usage) — CLI, API, workspace UI
- [Demo](#demo) — real logged output from live runs
- [Architecture](#architecture) — what every file does
- [How the pieces interact](#how-the-pieces-interact) — the call paths
- [Issue codes](#issue-codes) and [exit codes](#exit-codes)
- [Development](#development)

---

## How it works

Two passes see the same task and the same diff.

**Pass 1 — static.** No LLM, no network, no tokens, no API key. Four pure checks read the
parsed diff and the project's own files, and return findings. This pass alone is enough
for CI.

**Pass 2 — intent.** A model classifies every hunk against your task as `requested`,
`supporting`, or `unrequested`, and may raise issues Pass 1 structurally cannot see
(a request that was never fulfilled, a function nothing calls). It has four read-only
tools jailed to the repo. It can never mutate anything, and if it fails or you have no
API key, you still get the Pass 1 report.

A verdict is `clean`, `review`, or `fail`:

| Verdict | Meaning | Exit |
|---|---|---|
| `clean` | Nothing flagged | `0` |
| `review` | Something a human should look at | `1` |
| `fail` | At least one objective error | `1` |

Pass 2 can take a run from `clean` to `review` — scope creep alone is worth stopping for
— but it can never produce `fail`. Model judgement is never an objective error.

**`clean` does not mean correct.** It means no evidence of the failure modes vouch looks
for. It cannot tell you your logic is wrong.

---

## Setup

Requires **Python 3.12+** and **git**. The UI additionally needs **Node 20.19+** (CI uses 22).

### 1. Create the virtual environment

```bash
git clone https://github.com/nirajpankhania-cf/pyvouch.git
cd pyvouch
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

> **The `source` line is not optional.** `vouch` and `uvicorn` are installed *inside*
> `.venv/bin`, not on your system `PATH`. If you skip it you get
> `command not found: uvicorn`. Either activate the venv in every new terminal, or call
> the binaries by path: `.venv/bin/uvicorn …`. You can tell it worked because your prompt
> gains a `(.venv)` prefix and `which vouch` prints a path.

Check it:

```bash
vouch version          # 0.1.0
pytest                 # 471 passed
ruff check .
```

### 2. Add an API key (optional)

Pass 1 needs nothing. Pass 2 needs an Anthropic key.

```bash
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is gitignored. A real environment variable always wins over the file, so
`export ANTHROPIC_API_KEY=…` also works. Without a key, `vouch check` still runs and
tells you Pass 2 was skipped.

Get a key at [console.anthropic.com](https://console.anthropic.com/settings/keys).

### 3. Run it on a repo

```bash
cd /path/to/your/project
vouch check -m "what you asked the agent to do"
```

vouch reads the repo you are standing in. It never writes to it.

### 4. Run the API and workspace UI (optional)

Two terminals, because the UI is a separate dev server:

```bash
# terminal 1 — the API, pointed at the repo you want to review
source .venv/bin/activate
VOUCH_REPO_ROOT=/path/to/your/project uvicorn vouch.api.app:app --reload --port 8741

# terminal 2 — the workspace
cd frontend && npm install && npm run dev
```

Open **http://localhost:8742**.

### 5. Or use Docker

One command, one port, no Node or Python needed locally:

```bash
VOUCH_TARGET_REPO=/path/to/your/project docker compose up --build
```

Open **http://localhost:8741** — the image serves the API and the built UI together. The
target repo is mounted **read-only**, so "the verifier never mutates the repo" is enforced
by the filesystem rather than by good intentions.

> Honest caveat: the single-port serving this relies on is tested
> (`tests/test_api.py::TestServingTheBuiltUi`, plus a manual run against a real build),
> but the `docker compose` path itself has not been executed — there is no Docker on the
> machine this was written on. Treat the steps above as unverified.

### Environment variables

| Variable | What it does | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Enables Pass 2 | unset → Pass 1 only |
| `VOUCH_REPO_ROOT` | **Which repo the API reviews.** The CLI ignores this and uses your current directory. | the server's working directory |
| `VOUCH_DB` | Run-history database. Setting it also makes the CLI record runs without `--save`. | `$XDG_DATA_HOME/vouch/runs.db` |
| `VOUCH_ENV_FILE` | Read this file instead of `./.env` | `./.env` |
| `VOUCH_UI_DIR` | Where the built frontend lives | `frontend/dist` |

**About `VOUCH_REPO_ROOT`:** it only matters for the API and Docker. The CLI already knows
which repo you mean, because you are standing in it. The server has no current directory
you chose, so you must tell it which checkout to diff — that is all this variable is. In
Docker it is fixed to `/workspace`, the read-only mount point.

---

## Usage

### CLI

```
vouch check [OPTIONS]

  -m, --message TEXT   The task you gave the agent (highest priority)
      --staged         Check staged changes only
      --base REF       Diff against the merge-base with REF (pull-request style)
      --json           Machine-readable report
      --no-agent       Pass 1 only; no API key needed
      --model ID       Override the agent model
      --save           Record this run so the workspace UI can open it

vouch list-codes       Print every issue code and what it means
vouch version
```

The task is resolved in this order: `-m`, then a `TASK.md` file, then an interactive
prompt. (A Claude Code transcript loader slots in before the prompt; that is Phase 3 and
not built yet.) A task vouch inferred rather than was told is always echoed back, never
silently judged.

In CI:

```yaml
- run: vouch check --no-agent -m "${{ github.event.pull_request.title }}" --base origin/main
```

### HTTP API

Default port **8741**. Same report schema as `--json`.

| Route | Purpose |
|---|---|
| `POST /reviews` | Run the pipeline. `201` with the full report plus an `id`. |
| `GET /reviews` | History, newest first, summary fields only. `?limit=&offset=` |
| `GET /reviews/{id}` | Reopen a stored report. |
| `GET /health` | Liveness, version, repo root, whether a key is present. |

```bash
curl -X POST localhost:8741/reviews \
  -H 'content-type: application/json' \
  -d '{"task":"add retry to the pricing client","mode":"working","noAgent":true}'
```

`mode` is `working`, `staged`, or `base` (which also needs `baseRef`).

### Workspace UI

Three panels around one run: history on the left, the diff in the middle with every hunk
tagged `requested`/`supporting`/`unrequested`, findings on the right grouped by issue
code. Clicking a finding scrolls to the hunk containing its line. The header carries the
task, verdict, token cost, and **Copy JSON**.

The UI only ever calls the API. No check logic is reimplemented in TypeScript.

---

## Demo

Real output, captured from live runs against a small `shop` package
(a `PricingClient`, a `Cart`, and two tests). The task in both cases:

> add retry with exponential backoff to `PricingClient.fetch_price`

### A diff that does not match the task

The agent added a retry loop, but also imported a module it never wrote, left a TODO stub,
skipped an existing test, and wrote an unrelated README.

```
$ vouch check -m "add retry with exponential backoff to PricingClient.fetch_price"

Task (-m): add retry with exponential backoff to PricingClient.fetch_price

Pass 1 — static
  error unresolved-import          src/shop/client.py:6
        module not found in this repo: from shop.backoff import exponential_delay
  warn  placeholder-code           src/shop/client.py:26
        empty body added: 'warm_cache' has no implementation
  warn  placeholder-code           src/shop/client.py:27
        TODO/FIXME comment added: "# TODO: implement once the cache service lands"
  warn  test-tampering             tests/test_cart.py:11
        @pytest.mark.skip added (test disabled): "@pytest.mark.skip(reason="flaky in CI")"
  info  scope-drift                tests/test_cart.py
        'tests/test_cart.py' shares no tokens with the task — possibly out of scope
        (low confidence — heuristic)

Pass 2 — intent
  warn  request-unfulfilled        src/shop/client.py:6
        client.py imports exponential_delay from shop.backoff, but src/shop has no
        backoff module and exponential_delay is not defined anywhere, so the backoff
        isn't implemented and importing the client will fail.
  warn  dead-integration           src/shop/client.py:26
        The new warm_cache method is an empty TODO stub that nothing calls (a search
        finds only its definition).
  warn  unrequested-change         tests/test_cart.py:1
        The pytest import exists only so the skip marker in the next hunk works, and
        that skip is not part of the retry task.
  warn  unrequested-change         tests/test_cart.py:8
        Skipping an existing cart test as 'flaky in CI' has nothing to do with adding
        retry to fetch_price, and it hides test coverage.
  warn  unrequested-change         README.md:1
        A new README with deployment notes is unrelated to adding retry with backoff.

Unrequested hunks
  tests/test_cart.py:1-5 — The pytest import exists only so the skip marker in the next
        hunk works, and that skip is not part of the retry task.
  tests/test_cart.py:8-14 — Skipping an existing cart test as 'flaky in CI' has nothing
        to do with adding retry to fetch_price, and it hides test coverage.
  README.md:1-7 — A new README with deployment notes is unrelated to adding retry with
        backoff.

Pass 2 ran: 3 tool calls, 11831 tokens.

FAIL  1 error, 8 warn, 1 info  (10829ms)
$ echo $?
1
```

Worth noticing what each pass contributed. Pass 1 found the broken import by resolving it
against the repo — cheap, certain, no tokens. Pass 2 then explained the *consequence*:
the retry you asked for was never actually delivered, because the helper it calls does not
exist. That is `request-unfulfilled`, and no static check can reach it: the code is
syntactically fine and the intent is only knowable from the task. Pass 2 also used its
`search` tool to confirm nothing calls `warm_cache` before calling it dead.

### The same task, done properly

The helper now exists, has a test, and nothing unrelated was touched.

```
$ vouch check -m "add retry with exponential backoff to PricingClient.fetch_price"

Task (-m): add retry with exponential backoff to PricingClient.fetch_price

No findings.
Pass 2 ran: 3 tool calls, 11736 tokens.

CLEAN  no findings  (8429ms)
$ echo $?
0
```

The terminal report stays quiet when there is nothing to say, but the classifications are
still in the JSON — this is what the UI's middle panel renders:

```json
{
  "version": 1,
  "task": {
    "text": "add retry with exponential backoff to PricingClient.fetch_price",
    "source": "flag"
  },
  "agent": {
    "ran": true,
    "hunks": [
      {
        "file": "src/shop/client.py",
        "range": "1-24",
        "classification": "requested",
        "reason": "Adds the retry loop with exponential backoff and a max_retries option to PricingClient.fetch_price, which is exactly what the task asked for."
      },
      {
        "file": "src/shop/backoff.py",
        "range": "1-6",
        "classification": "supporting",
        "reason": "Adds the exponential_delay helper that the new retry loop in fetch_price calls."
      },
      {
        "file": "tests/test_cart.py",
        "range": "15-25",
        "classification": "supporting",
        "reason": "Adds a test checking that the new backoff delay doubles on each attempt."
      }
    ],
    "summary": "The diff matches the task: fetch_price now retries with exponential backoff, using a new helper that has a test. There are no unrequested changes.",
    "cost": { "inputTokens": 6921, "outputTokens": 390, "toolCalls": 1 }
  },
  "verdict": "clean"
}
```

`supporting` is the distinction that makes this usable. A new `backoff.py` was not literally
asked for, but it exists *because* of the task — flagging it would be noise. An unrelated
README is `unrequested`. Collapsing those two into "extra files changed" is what makes
most diff tooling unhelpful.

Both runs cost roughly 12k and 7k tokens. `--no-agent` costs nothing at all and still
catches the import, the stub and the skipped test.

### Dogfooding

vouch runs on its own diffs. `docs/CAUGHT.md` logs the real findings, including the
false positives that changed the design — and one occasion where the live agent read an
untracked `.env` straight into a prompt, which is why credentials are now withheld from
Pass 2.

---

## Architecture

```
src/vouch/
  core/        pipeline, types, verdict      ← the contract
  context/     diff, task, project access    ← reads the world
  checks/      Pass 1                        ← pure functions
  agent/       Pass 2                        ← the only network hop
  store/       run history
  api/         HTTP
  cli.py       terminal entry point
frontend/      React workspace
```

Four rules shape the whole layout:

1. **One core, many surfaces.** CLI, API and UI all call `run_pipeline`. No surface has
   its own check logic.
2. **Checks are pure.** `(CheckContext) -> list[Finding]`. No I/O, no network, no LLM.
   Everything from disk is loaded up front into `ProjectAccess`.
3. **The verifier never mutates the repo.** Every git call is read-only; the agent's tools
   cannot write.
4. **Exit codes, issue codes and the JSON schema are API.** They live in `docs/SPEC.md` and
   do not change casually.

### `src/vouch/core/` — the contract

| File | What it does |
|---|---|
| `types.py` | Every shared model. `Hunk`/`HunkLine` (the parsed diff), `TaskInfo`, `Finding`, `ProjectAccess`, `CheckContext`, and the issue-code registries. `WireModel` is the base that gives every model camelCase JSON keys while Python attributes stay snake_case. `Hunk.range` is the single definition of a hunk's label — the agent echoes it and the UI joins on it. |
| `report.py` | Builds the report and owns `verdict_of`, the one place a verdict is decided. Also `agent_findings_of`, which turns the model's classifications into issue-coded findings: model findings are forced to `warn`, and one `unrequested-change` is derived per unrequested hunk so derivation stays the single source. |
| `pipeline.py` | `run_pipeline` — resolve the task, get the hunks, build `ProjectAccess`, run every check, optionally run the agent, assemble the report. A check that raises becomes `CheckCrashError` (exit 2), never a finding: a bug in vouch must not masquerade as a problem in your code. |

### `src/vouch/context/` — reading the world

| File | What it does |
|---|---|
| `gitcmd.py` | The only module that invokes git. Every call is read-only and uses `core.quotepath=false` so non-ASCII paths come back usable. |
| `diff.py` | Hand-written unified-diff parser over git's porcelain output — renames, binary files, mode-only changes, omitted `@@` counts, missing trailing newlines, paths with spaces. In working-tree mode, untracked files are synthesized as added hunks, because a file an agent just wrote is exactly what vouch exists to inspect. `--base` uses three-dot merge-base semantics, matching what a pull request shows. |
| `task.py` | Resolves the task: `-m`, then `TASK.md`, then an injectable transcript loader (none shipped yet — Phase 3), then an interactive prompt. An inferred task is always echoed. |
| `project.py` | The largest and fiddliest module. Discovers the **target** repo's virtualenv (never vouch's own), indexes its importable modules and their exported names, and maps each directory to its importable siblings. If no environment can be found it says so, and the imports check then declines to judge third-party names rather than call a real dependency a hallucination. |

### `src/vouch/checks/` — Pass 1

Each check is a pure function. `registry.py` lists them; nothing else knows the order.

| File | What it catches |
|---|---|
| `placeholders.py` | `placeholder-code`. TODO/FIXME comments and "not implemented" phrases in *added* lines, plus empty function bodies found via the AST. Exempts abstract methods, `Protocol` bodies, `.pyi` stubs and `@overload`, which are empty on purpose. A comment that *quotes* a marker is prose, not a stub. |
| `tests.py` | `test-tampering`. Skip markers added, assertions removed (counted as a net delta, so a refactor that moves assertions stays silent), and deleted test files — the last of which is an `error`. |
| `imports.py` | `unresolved-import`. Resolves added imports against the repo and the target environment. Exempts imports inside `try/except ImportError`, since an import allowed to fail is an optional dependency, and sibling modules, since Python puts a file's own directory on `sys.path`. |
| `scope.py` | `scope-drift`. Token overlap between the task and each changed file. Always `info`/`low`, and it refuses to judge a task of fewer than two meaningful words. A deliberately weak signal that asks a question rather than making a claim. |
| `shared.py` | Helpers the checks share: is this a code file, a test file, which lines were added, parse this Python safely. |

### `src/vouch/agent/` — Pass 2

| File | What it does |
|---|---|
| `loop.py` | A hand-written agent loop: send task and hunks, service tool calls, validate the JSON verdict, retry once, then degrade. The client is injected, so the whole loop is testable offline against a scripted fake. Two guards matter: when the tool budget is spent the tools are **withdrawn from the request** rather than the model being asked politely to stop, and classifications for hunks that were never sent are dropped, so a fabricated path cannot reach the report. |
| `prompts.py` | The rubric, the per-code issue guides, and hunk rendering. |
| `schema.py` | The pydantic shape of a verdict. Rejects issue codes outside the agentic taxonomy. |
| `tools.py` | The four read-only tools — `read_file`, `read_git_log`, `list_dir`, `search` — every path jailed to the repo root, every result capped, and no tool ever raises into the loop: failures come back as error strings the model can react to. |
| `secrets.py` | Which filenames must never have their contents leave the machine. Pass 2 is the only network hop, so `.env`, `*.pem`, `id_rsa` and friends are classified by **path only**: prompts show a line count, `read_file` refuses, `search` drops their matches. Such a hunk is still flagged — an unrequested `.env` is worth knowing about — just never quoted. |

### Surfaces and support

| File | What it does |
|---|---|
| `cli.py` | Typer entry point. Parses flags, calls `run_pipeline`, renders or dumps JSON, exits with the code. |
| `render.py` | Terminal output: the two passes, unrequested hunks, cost, verdict line. The only module that knows about colour. |
| `envfile.py` | A ~40-line `.env` reader. A real environment variable always wins over the file. |
| `store/runs.py` | Run history in one SQLite table via stdlib `sqlite3`. The report is stored whole as JSON, with verdict and counts duplicated into columns so the history list never parses a report. |
| `api/app.py` | FastAPI. Thin handlers: validate, call the core, return the report. Serves the built UI at `/` when there is one. |
| `api/schemas.py` | Request and response shapes, reusing the same report models as the CLI. |

### `frontend/`

| File | What it does |
|---|---|
| `src/types.ts` | The wire format, mirroring the pydantic models. Python is authoritative. |
| `src/api.ts` | The only module that talks to the API. |
| `src/App.tsx` | Shell and state: health, history, the open run, the jump target. |
| `src/components/RequestForm.tsx` | Task, diff mode, Pass-1-only toggle. |
| `src/components/HistoryList.tsx` | Past runs, newest first. |
| `src/components/VerdictHeader.tsx` | Verdict, task, counts, cost, Copy JSON. |
| `src/components/ChangePanel.tsx` | The diff, with each hunk's classification tag. |
| `src/components/FindingsPanel.tsx` | Findings grouped by code; click to jump. |
| `src/format.ts` | Labels, relative times, cost estimate. |
| `src/main.tsx`, `src/styles.css` | Mount point and all styling. Hand-written CSS; three panels and a form do not need a component library. |

### Tests

`tests/` mirrors `src/`. Beyond the unit tests, two things are worth knowing:
`tests/fixtures/diff/` holds ten diffs captured from real git output (renames, binary,
spaces in paths, missing trailing newline) so the parser is tested against git rather than
against my idea of git; and `tests/contract/hunk-ranges.json` pins the hunk-range format
and is asserted from **both** pytest and vitest, because a mismatch between the two
implementations silently drops classification tags in the browser and would otherwise be
invisible.

---

## How the pieces interact

A `vouch check` run, end to end:

```
cli.py
  └─ core/pipeline.run_pipeline
       ├─ context/task.resolve_task ─────────── -m → TASK.md → prompt
       ├─ context/diff.get_hunks ─────────────── context/gitcmd (read-only git)
       │                                         └─ list[Hunk]
       ├─ context/project.build_project_access ─ target venv, module index, siblings
       │                                         └─ ProjectAccess
       ├─ CheckContext(hunks, task, project)
       ├─ checks/registry.ALL_CHECKS ─────────── placeholders, tests, imports, scope
       │                                         └─ list[Finding]
       ├─ agent/loop.run_agent (if enabled) ──── agent/prompts, agent/tools
       │                                         └─ AgentSection
       └─ core/report.build_report ───────────── verdict_of(findings, agent)
                                                 └─ VouchReport
  ├─ render.render            → terminal
  ├─ report.to_json_dict      → --json
  ├─ store/runs.save          → --save
  └─ report.exit_code_for     → 0 / 1
```

The same `run_pipeline` serves the API, which adds persistence and returns the report over
HTTP; the UI then reads that report and nothing else.

Three details explain most of the design:

**`ProjectAccess` is why checks are pure.** Everything a check could want from disk — file
contents, the target environment's installed modules, each directory's importable siblings
— is loaded once in `project.py` before any check runs. A check is then a function from
data to findings, which is why they are fast and trivially testable.

**`Hunk.range` is a cross-language join key.** The agent is shown `range=1-5`, echoes it
back in its verdict, and the UI uses the pair `(file, range)` to attach a classification
to a hunk in `changes`. Three consumers, one definition, pinned by a shared test fixture.
This is not over-engineering: live testing caught the model returning `[1-5]` with the
prompt's brackets, which cost the finding its line number and would have silently dropped
the tag in the browser.

**The report is self-contained.** `changes` carries the parsed diff, so a stored run stays
inspectable after the working tree has moved on. Without it, reopening history would show
findings pointing at a diff nobody still has.

---

## Issue codes

`vouch list-codes` prints these with descriptions. They are stable API.

**Pass 1 (deterministic)**

| Code | Meaning |
|---|---|
| `placeholder-code` | Stub, TODO, or empty body added |
| `test-tampering` | Test skipped, assertions removed, or test file deleted |
| `unresolved-import` | Import that does not resolve in this repo or environment |
| `scope-drift` | A changed file shares no vocabulary with the task |

**Pass 2 (agentic)**

| Code | Meaning |
|---|---|
| `request-unfulfilled` | The task was not actually done |
| `unrequested-change` | A hunk the task does not account for |
| `unintended-removal` | Something was deleted that nobody asked to delete |
| `dead-integration` | New code nothing calls |
| `instruction-file-disobeyed` | Breaks the repo's own stated conventions |
| `docs-drift` | Docs now contradict the code |
| `change-narration` | The agent's account of its changes contradicts the diff *(reserved)* |
| `misleading-claim` | The agent claimed something the evidence contradicts *(reserved)* |

The last two are registered but not yet emitted: judging an agent's *claims* needs its
transcript, which is Phase 3. Codes are API, so they are reserved now rather than added
later. `unrequested-change` is also never emitted by the model — it is derived from the
hunk classifications, so there is exactly one source for it.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | `clean` |
| `1` | Findings (`review` or `fail`) |
| `2` | Tool error — bad ref, not a git repo, a check crashed |

`2` always means *vouch* failed, never that your code did. Treat it differently in CI.

---

## Development

```bash
source .venv/bin/activate
pytest                  # 471 tests
ruff check .
cd frontend && npm test && npm run typecheck && npm run build
```

Project documents:

- **`docs/SPEC.md`** — the contract: schema, codes, verdict rule, API. Authoritative.
- **`docs/PLAN.md`** — phased build plan and what is done.
- **`docs/DECISIONS.md`** — every meaningful technical choice, as `choice over alternative: reason`.
- **`docs/CAUGHT.md`** — real findings from running vouch on itself, false positives included.

If the SPEC and the code disagree, the SPEC wins — and if the contract genuinely needs to
move, move it in the same change.

## Non-goals

General code review, linting, auto-fixing your code, a cloud SaaS, and non-Python AST
depth. vouch answers one question and tries to answer it well.

## License

AGPL-3.0-only.
