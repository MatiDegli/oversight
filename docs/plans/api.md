# Plan: the API and the backend wiring, level 1 (front 16)

Revision 2, after the review of ee2ac14. Sources: AGENTS.md, docs/proposal.md (3, 4.5 to 4.7, 5,
7), `contracts/`, main at 479b3ec, `changes/manager` f93d3e4 (`open`, `run_ended`, `approve`,
`reject`, `aclose`, `ChangeNotFound`, `WrongState`) plus three additions asked of it (section 4).
No contract change, no new dependency or module: starter in `app`, routes in `api`.

## 1. Startup and shutdown (`app.py`, `settings.py`, `__main__.py`, `tools/smoke.py`)

`Workspace.reset` (template copy, `git init -b main`, fixed initial commit), `acquire_runtime`,
`ensure_demo_venv`, `EventStore`, `WorkspaceGraph` and `app.state.layout` exist; nothing calls them
yet, and `make smoke` runs replay. **Settings** gains `agent_model` (`muse-spark`, or `fake` with
`fake_script`), the budget and prices (section 3), and `demo_python` (tests only, no variable).
Limits are constants in `app.py`, no variables: 30 steps, 4096 output tokens, 600 s, tests 60 s,
git 30 s, cone depth 4 and 40 nodes. The pytest pin is read from `tools/demo-venv-requirements.txt`
(`pytest==9.1.1`), so the demo venv and `make demo-repo-check` never diverge.

`create_app(settings, client_factory=None)` builds the `Workspace`, the run id
(`run-YYYYMMDD-HHMMSS`, next second if taken) and its `EventStore`. The lifespan: (1)
`acquire_runtime()` (a second server fails with `WorkspaceBusy`), `reset()`, read the ledger; (2)
`store.start()`, `run.started` with `PROMPT_VERSION`, `base_commit`, the profile, slots `[A]`, the
limits, `Budget(budget_usd or 0, ledger spent_usd, [0.5, 0.8], 0.9)`; (3) `scan(base)` in a thread
with the layout, `graph.snapshot`; (4) `ChangeManager(..., git_timeout_s=30)` as the sink, its
`publish` wrapping `store.publish` to watch `change.resolved` (the slot) and `usage.tick` (the
ledger); (5) the demo venv build as a task, awaited by `POST /agents`; a failed build is an `error`
(source `tests`, not fatal) and the run's `run_tests` reports `error`.

**Shutdown**, in the lifespan's `finally`, each step even if one before failed: (a) cancel the run
task and await it (`run_process` kills its process group), its `finally` calling `run_ended(agent,
None)`; (b) cancel the venv task and await it, so its subprocess is killed and reaped; (c) `await
manager.aclose()`, which waits and drains (section 4); (d) check main (section 4); (e)
`store.close()`, which commits every queued event and ends SSE streams; (f) `release_runtime()`.
`serve` gets `timeout_graceful_shutdown=1`. `tools/smoke.py` runs `serve` with the fake model.

## 2. Routes (`api/routes.py`, new `api/commands.py`, `api/__init__.py`)

`create_router(store, mode, commands=None)`: replay answers 501 on commands; live passes the
manager and a `Starter` protocol `api` defines and `app` implements. Errors: `ApiError`,
`ERROR_STATUS`, `{"error": {...}}`.

| Route | Success | Errors |
| --- | --- | --- |
| `POST /agents` `StartAgent` | 201 `AgentCreated` once `open` returned | `invalid_request` 422, `spend_brake` 403, `wrong_state` 409, `slot_busy` 409 |
| `POST /changes/{id}/approve` or `reject`, `{comment?}` | 202, empty; results as events | `not_found` 404, `wrong_state` 409 |
| `GET /graph`, `/events`, `/health` | as today, `mode="live"` | as today |
| stop, request-changes, impact, files, rescan, message, pause, resume | 501 `not_implemented`, body not parsed | |

- **Start: validate, reserve, release on every exit.** Side-effect-free checks, in order: the
  request (`model` is the configured profile and not in `UNVERIFIED_PROFILES`, slot `A`, a task)
  and a Meta profile's key: `invalid_request`; the brake: `spend_brake`; main suspect:
  `wrong_state`; the slot: `slot_busy`, then the claim, with no `await` between them. Everything
  after (venv wait, ids `agent-n`/`change-n`, client, `open`) runs in a `try` that, on any exception
  or cancellation (shutdown, client gone), releases the slot, publishes an `error` for a failure and
  re-raises. Once `open` returns, the run task is created with no `await` between, and the slot
  stays held until a `change.resolved` (`rejected` or `merged`).
- **Unexpected failures.** With `internal` (500) in the contract, one handler answers every
  unexpected failure, `open` included, as `ErrorResponse` `internal`, no traceback. Without it, that
  handler answers `wrong_state` 409, "the server failed: ...; see the error event". Waits for him.
- **Decisions.** `ChangeNotFound` (a malformed id too) is `not_found`; `WrongState`, or approve with
  main suspect, is `wrong_state` with the reason; otherwise 202. No body means no comment.

## 3. Spending brake (`settings.py`, `agents/usage.py`, `app.py`, `__main__.py`, `.env.example`)

- **Configured, or closed.** `OVERSIGHT_BUDGET_USD`, `MUSE_SPARK_INPUT_USD_PER_M`,
  `MUSE_SPARK_OUTPUT_USD_PER_M` from `.env`; `.env.example` documents 150, 0.10 and 0.20 for
  `muse-spark-1.3-contributor`. No defaults: a missing, non-numeric or non-positive value makes
  every Meta start `spend_brake` ("budget or prices not configured"). Cached input prices as fresh
  input, an upper bound. The fake profile has no price and never touches the ledger.
- **Ledger** `.runtime/ledger.json`: `{spent_usd, unknown: [{run_id, agent_id, model, ts}]}`.
  `reset` keeps it (it deletes only `worktrees`, `workspace`, `tmp`). At start, missing is 0;
  unreadable or malformed fails closed (`spend_brake`, and an `error`). Written atomically (temp
  file in `.runtime/`, `fsync`, `os.replace`) in a thread under one `asyncio.Lock`. The run's
  `UsageLedger` starts from it, so ticks count across restarts.
- **Each tick** of a Meta run is persisted by the publish wrapper before it passes on: a known
  `cost_usd` adds to `spent_usd`; null (usage not reported) appends the run to `unknown`. A hard
  kill between a reply and its write loses at most that call, inside the 10% margin.
- **At start**, a Meta profile is blocked if unconfigured, the file unreadable, `unknown`
  non-empty, or `spent_usd > 0.9 x budget` (above US$135 of 150). From 50% (US$75) and 80%
  (US$120) it starts and `usage.tick` carries `warn_50` / `warn_80`. A running agent is not cut.
- **The maintainer acts**: `uv run python -m oversight ledger` prints the file; `ledger settle
  --spent-usd X` sets `spent_usd` to Meta's console total and clears `unknown`. It takes the
  runtime lock, so it refuses while a server runs.

## 4. Concurrency, main's state, drain

- **A second decision while one runs**: `_take` flips the change to `deciding` with no `await`, so
  a second Approve or Reject is `WrongState`, 409, before any git call; also on `closing`, `closed`.
- **`broken`** (the merge and `merge --abort` failed, or the facts are unreadable): the manager
  sends its `error` and refuses Approve. The API holds main suspect until restart: `POST /agents`
  and approve answer `wrong_state`, "main's state is unknown: reject and restart"; Reject still
  closes the change. **Asked of the manager**: a read-only `main_suspect`, set on entering
  `broken`, and `async check_main() -> bool | None` (`MERGE_HEAD`, `status --porcelain`, under
  main's lock; `None` if git fails).
- **Shutdown mid-merge**: `aclose` waits for the decision. Step (d): unless `check_main()` is
  `True`, publish `error` (source `git`, fatal), "main was left mid-merge or dirty; the next
  start's reset rebuilds it", before the store closes: never a silent `MERGE_HEAD`.
- **Drain, asked of the manager**: `aclose()` waits for decisions in flight, runs the close-out
  retry once more (unsent resolution, delta, `done`, in order, twice each), and publishes an
  `error` naming each change and event still unsent. `store.close()` only follows `aclose()`.

## 5. Models, and credits in tests

`muse-spark` builds `OpenAIClient(MUSE_SPARK, base_url=META_BASE_URL, ...)`:
`muse-spark-1.3-contributor`, reasoning `low` on every call; `fake` builds `ScriptedModel`. Tests
build `Settings(...)` or load a `tmp_path` file, and may pass `client_factory` (a scripted model
under the Meta profile, for the brake). **Global guard**, root `conftest.py`, autouse for every
test: strip `META_API_KEY` and `BASETEN_API_KEY`, and patch the real transports of `httpx2` and
`httpx` (sync and async) to raise `NetworkBlocked` for any host but `127.0.0.1`, `localhost`,
`::1`. The scripted model, `MockTransport` clients, the probe's `FakeTransport` and loopback
servers keep working. `backend/tests/test_no_credits.py`, in `make check`: an `OpenAIClient` at
`META_BASE_URL` with `test-key` raises `NetworkBlocked`; an exported key is absent in a test.

## 6. What the frontend may rely on (front 17)

`change_id` comes from events. 202 is accepted, not merged: `change.resolved`, `graph.delta`
(cause `merge`), `agent.status` `done`; Reject sends no delta. Buttons stay disabled until
`change.resolved`, a 4xx, or an `error` naming the change; show a 409's message. Do not assume a
delta with nodes, `merge == "merged"`, slot `B`, a fixed agent id, Request changes or Stop.

## 7. Tests (`backend/tests/api`, `e2e`, `test_no_credits.py`; then `make smoke`, `make check`)

Real uvicorn on a free port, `asyncio.timeout(60)`, waits on events, envelopes validated against
`ServerEvent`, runtime in `tmp_path`.

- `api/test_commands.py`: bad ids 404; approve before the proposal 409; a second decision 409 and
  one `change.resolved`; a racing start `slot_busy`; slot `B`, empty task, Muse Spark Open, no key
  422; the 501s. `api/test_start.py`: a failed `open` answers the error body and the next start is
  201; a start cancelled while `open` waits on an event leaves the slot free.
- `api/test_brake.py`: no budget, a missing price, a malformed ledger, an `unknown` entry, 135.01
  of 150: `spend_brake` before any client; 75 and 120 start with `warn_50`, `warn_80`; replies
  without usage write `unknown` and refuse the next start, also after a restart and `reset`;
  `settle` clears it; a write failing before `os.replace` keeps the old file; fake never braked.
- `test_app.py`: `run.started` with the ledger's spend, `graph.snapshot` with positions, seq 2.
- `e2e/test_level1.py`, a fake script (read `shop`, add a function, run tests, finish), two
  approved runs: `change.updated`, `change.proposed`, 202, `change.resolved`, `graph.delta`, `done`
  in order; the second delta's `from_version` is the first's `to_version`; `GET /graph` holds both
  nodes. A third run's reject: no delta.
- `e2e/test_shutdown.py`: shutdown mid-run ends the run, closes the store, frees the lock; reaps a
  venv subprocess blocked on stdin (pid gone) before the store closes; waits for a held `merge` and
  logs its `change.resolved`; drains a delta whose publish failed; with `MERGE_HEAD` left and a
  failing abort: `broken`, approve and start 409, reject 202, shutdown's `error` logged.

About 450 lines of code and 600 of tests, four commits: startup, brake, routes, guard.

## Open

1. **Stop** is level 2 in AGENTS.md and the manager has none: 501 now. **`internal` -> 500**: the
   maintainer's answer picks the branch in section 2.
2. **Prices** US$0.10 / 0.20 per million: the coordinator's figures, to be confirmed. Limits too.
3. **Manager additions** (`main_suspect`, `check_main`, the drain): this front waits for them.
4. **Files the first brief did not list**: root `conftest.py`, `.env.example`, `agents/usage.py`.
