# Plan: the API and the backend wiring, level 1 (front 16)

Revision 2 answers the plan review of ee2ac14. Sources: AGENTS.md, docs/proposal.md (3, 4.5 to
4.7, 5, 7), `contracts/`, main at 479b3ec, and the change manager on `changes/manager` f93d3e4
(`ChangeManager`, `ChangeNotFound`, `WrongState`; `open`, `run_ended`, `approve`, `reject`,
`aclose`), plus two additions asked of it (section 4). No contract change, no new dependency, no
new backend module: the starter lives in `app`, the routes in `api`, the ledger file in
`agents/usage.py`.

## 1. Startup and shutdown (`app.py`, `settings.py`, `__main__.py`, `tools/smoke.py`)

**Today** `Workspace.reset` copies `demo-repo/` to `.runtime/workspace/`, runs `git init -b main`
and makes the fixed initial commit; `acquire_runtime`, `ensure_demo_venv`, `EventStore`,
`WorkspaceGraph(root, layout)` and `app.state.layout` exist, but nothing calls them: the live app
has no lifespan, store or routes, and `make smoke` runs replay. **Settings** gains `agent_model`
(`muse-spark`, or `fake` with `fake_script`), the budget and prices (section 3), and `demo_python`,
with no variable, for tests only. Limits are constants in `app.py`, with no variables: 30 steps,
4096 output tokens, 600 s, tests 60 s, git 30 s, cone depth 4 and 40 nodes. The pytest pin is read
from `tools/demo-venv-requirements.txt` (`pytest==9.1.1`), so the demo venv and `make
demo-repo-check` never diverge.

`create_app(settings, client_factory=None)` builds the `Workspace`, the run id
(`run-YYYYMMDD-HHMMSS`, next second if that log exists) and its `EventStore`. The lifespan:

1. `acquire_runtime()` (a second server fails with `WorkspaceBusy`); `base = reset()`; read the
   ledger file (section 3).
2. `store.start()`; publish `run.started`: `PROMPT_VERSION`, `base_commit`, the profile, slots
   `[A]`, the limits, `Budget(budget_usd or 0, ledger spent_usd, [0.5, 0.8], 0.9)`.
3. `WorkspaceGraph(main.root, app.state.layout).scan(base)` in a thread; publish `graph.snapshot`.
4. `ChangeManager(workspace, publish, main_graph=..., main_snapshot=..., git_timeout_s=30)`, the
   runtime's sink. `publish` wraps `store.publish` to watch `change.resolved` (the slot) and
   `usage.tick` (the ledger).
5. The demo venv build starts as a task; `POST /agents` awaits it. A failed build is an `error`
   (source `tests`, not fatal); the run goes on and its `run_tests` reports `error`.

**Shutdown**, in the lifespan's `finally`, each step even if the one before failed: (a) cancel the
run task and await it (tools and `run_process` kill their process groups on cancellation), its
`finally` calling `run_ended(agent, None)`; (b) cancel the venv task and await it, so its
subprocess is killed and reaped; (c) `await manager.aclose()`, which waits and drains (section 4);
(d) check main (section 4); (e) `store.close()`, which commits every queued event and ends the SSE
streams; (f) `release_runtime()`. `serve` gets `timeout_graceful_shutdown=1`, as replay has.
`tools/smoke.py` switches to `("serve",)` with the fake model.

## 2. Routes for level 1 (`api/routes.py`, new `api/commands.py`, `api/__init__.py`)

`create_router(store, mode, commands=None)`: replay answers 501 on commands; live passes the manager
and a `Starter` protocol that `api` defines and `app` implements, as with `ChangeSink`. Errors go
through `ApiError` and `ERROR_STATUS`: `{"error": {...}}`.

| Route | Success | Errors |
| --- | --- | --- |
| `POST /agents` `StartAgent` | 201 `AgentCreated` once `open` returned | `invalid_request` 422, `spend_brake` 403, `wrong_state` 409, `slot_busy` 409 |
| `POST /changes/{id}/approve` `{comment?}` | 202, empty; results as events | `not_found` 404, `wrong_state` 409 |
| `POST /changes/{id}/reject` `{comment?}` | 202, empty | `not_found` 404, `wrong_state` 409 |
| `GET /graph`, `/events`, `/health` | as today, `mode="live"` | as today |
| `POST /agents/{id}/stop`, request-changes, `GET /impact`, `GET`/`PUT /files`, rescan, message, pause, resume | 501 `not_implemented`, body not parsed | |

- **Start: validate, reserve, release on every exit.** Checks without side effects, in order: the
  request (`model` names the configured profile, slot `A`, a task; a profile in
  `UNVERIFIED_PROFILES` such as Muse Spark Open) and, for a Meta profile, its key: `invalid_request`;
  the brake: `spend_brake` (section 3); main suspect: `wrong_state` (section 4); the slot:
  `slot_busy`. The slot check and the claim have no `await` between them, so a racing start gets
  `slot_busy`. All after the claim (venv wait, ids `agent-n`/`change-n`, client, `open`) runs in a
  `try` that, on any exception or cancellation (shutdown, the client gone), releases the slot,
  publishes an `error` for a failure, and re-raises. Once `open` returns, the run task is created
  with no `await` in between and owns the slot until a `change.resolved` (`rejected` or `merged`).
- **Unexpected failures (Open 2).** With `internal` (500) in the contract: one exception handler
  answers every unexpected failure, `open` included, as `ErrorResponse` `internal`, no traceback.
  Without it: the same handler answers `wrong_state` 409, "the server failed: ...; see the error
  event", so every body validates. Implementation waits for the maintainer's answer.
- **Decisions.** `ChangeNotFound` is `not_found` (a malformed id too); `WrongState`, or approve
  while main is suspect, is `wrong_state` with the reason; otherwise 202. No body, no comment.

## 3. Spending brake (`settings.py`, `agents/usage.py`, `app.py`, `__main__.py`, `.env.example`)

- **Configured, or closed.** `OVERSIGHT_BUDGET_USD`, `MUSE_SPARK_INPUT_USD_PER_M` and
  `MUSE_SPARK_OUTPUT_USD_PER_M` come from `.env`. `.env.example` documents 150, 0.10 and 0.20 for
  `muse-spark-1.3-contributor` (prices still to be confirmed). Settings has no defaults: a missing,
  non-numeric or non-positive value makes every Meta start `spend_brake`, "budget or prices not
  configured". Cached input is priced as fresh input, an upper bound. The fake profile has no price
  and never touches the ledger.
- **Ledger file** `.runtime/ledger.json`, `{spent_usd, unknown: [{run_id, agent_id, model, ts}]}`.
  `reset` keeps it (it deletes only `worktrees`, `workspace`, `tmp`). Read at start: missing is 0
  and none unknown; unreadable or malformed fails closed (`spend_brake`, and an `error` at start).
  Written atomically: temp file in `.runtime/`, `fsync`, `os.replace`, in a thread, under one
  `asyncio.Lock`. The run's `UsageLedger` starts from it, so ticks count across restarts.
- **Every tick** of a Meta run is persisted by the publish wrapper before it passes on: a known
  `cost_usd` adds to `spent_usd`; a null one (the API reported no usage) appends the run to
  `unknown`. A hard kill between a reply and its write loses at most that call, inside the margin.
- **At start**, for Meta profiles, blocked if: unconfigured, the file unreadable, `unknown`
  non-empty, or `spent_usd > 0.9 x budget` (above US$135 of 150). At 50% (US$75) and 80% (US$120)
  it starts, and `usage.tick` carries `warn_50` / `warn_80`. A running agent is not cut: the brake
  refuses new runs (AGENTS.md).
- **The maintainer acts** with `uv run python -m oversight ledger`, which prints the file, and
  `ledger settle --spent-usd X`, which sets `spent_usd` to the total Meta's console shows and clears
  `unknown`. It takes the runtime lock, so it refuses while a server runs.

## 4. Concurrency, main's state, drain

- **A second decision while one runs**: `_take` flips the change to `deciding` with no `await`, so
  the second Approve or Reject raises `WrongState`: 409 before any git call. Also on `closing`
  and `closed`. Stop is 501 at level 1.
- **`broken`** (the merge failed and `merge --abort` failed, or the facts are unreadable): the
  manager sends its `error` and refuses Approve. The API then holds main suspect until restart:
  `POST /agents` and approve answer `wrong_state`, "main's state is unknown: reject and restart";
  Reject still closes the change. **Asked of the manager** (fix round 3): a read-only `main_suspect`
  set when a change enters `broken`, and `async check_main() -> bool | None` reading `MERGE_HEAD`
  and `status --porcelain` under main's lock (`None` if git fails).
- **Shutdown mid-merge**: `aclose` waits for the decision (merged, `ready` after the abort, or
  `broken`). Step (d): unless `check_main()` is `True`, publish `error` (source `git`, fatal), "main
  was left mid-merge or dirty; the next start's reset rebuilds it", before the store closes: never
  a silent `MERGE_HEAD`. A forced exit skips this; the next `reset` and `run.started` recover.
- **Drain. Asked of the manager**: `aclose()` waits for the decisions in flight, then runs the
  close-out retry once more (`_finish_pending`: unsent resolution, delta and `done`, in order,
  twice each), and publishes an `error` naming each change and event still unsent: the log is never
  silently incomplete. `store.close()` runs only after `aclose()` returned.

## 5. Models, and credits in tests

`muse-spark` builds `OpenAIClient(MUSE_SPARK, base_url=META_BASE_URL, api_key=...,
max_output_tokens=...)`: `muse-spark-1.3-contributor`, reasoning `low` on every call. `fake` builds
`ScriptedModel(load_script(...))`. Tests build `Settings(...)`, or `Settings.load` on a `tmp_path`
file, and may pass `client_factory` (a scripted model under the Meta profile, for the brake).
**Global guard** in the root `conftest.py`, autouse for every test (contracts, backend, tools):
strip `META_API_KEY` and `BASETEN_API_KEY`, and patch the real transports of `httpx2` and `httpx`
(sync and async) to raise `NetworkBlocked` for any host but `127.0.0.1`, `localhost`, `::1`. The
scripted model, the `MockTransport` clients, the probe's `FakeTransport` and the loopback uvicorn
servers keep working; a client aimed at Meta or Baseten fails before a byte leaves.
`backend/tests/test_no_credits.py`, run by `make check`: an `OpenAIClient` at `META_BASE_URL` with
`test-key` raises `NetworkBlocked`; a key exported to the run is absent inside a test.

## 6. What the frontend may rely on (front 17)

- `POST /changes/{change_id}/approve|reject` with `{}` or `{comment}`; `change_id` comes from
  events (`agent.status`, `change.proposed`). 202 means accepted, not merged: `change.resolved`,
  then `graph.delta` (cause `merge`), then `agent.status` `done`. Reject sends no delta.
- Both buttons stay disabled from the click until `change.resolved`, a 4xx, or an `error` naming
  the change. Show a 409's message: it may say main is suspect. Do not assume a delta with nodes,
  `merge == "merged"`, slot `B`, a fixed agent id, Request changes or Stop (501). No key approves.

## 7. Tests: `uv run pytest backend/tests/api backend/tests/e2e backend/tests/test_no_credits.py`

Real uvicorn on a free port; `asyncio.timeout(60)`; waits on events, never sleeps; every envelope
validated against `ServerEvent`; runtime in `tmp_path`. Then `make smoke` and `make check`.

- `api/test_commands.py`: unknown and malformed ids 404; approve before the proposal 409; a second
  decision while one runs 409, one `change.resolved`; a racing start `slot_busy`; slot `B`, an empty
  task, Muse Spark Open, a missing key 422; each 501 route; replay's 501s.
- `api/test_start.py`: a failed `open` answers the error body and the next start is 201; a start
  cancelled while `open` waits on an event leaves the slot free.
- `api/test_brake.py`: no budget, a missing price, a malformed ledger, an `unknown` entry, spent
  135.01 of 150: each `spend_brake` before any client; spent 75 and 120 start, ticks `warn_50` and
  `warn_80`; replies without usage write `unknown` and refuse the next start, also after a restart
  and `reset` on the same runtime dir; `ledger settle` clears it; a write failing before
  `os.replace` leaves the old file. The fake profile is never braked.
- `test_app.py`: startup publishes `run.started` with the ledger's spend, then `graph.snapshot`
  with the layout's positions; `GET /graph` answers at seq 2.
- `e2e/test_level1.py`, a fake script that reads `shop`, adds a function, runs tests, finishes; two
  runs, each approved: `change.updated`, `change.proposed`, 202, `change.resolved`, `graph.delta`,
  `agent.status` `done`, in that order; the first delta's `from_version` is the snapshot's, the
  second's the first's `to_version`; `GET /graph` holds both nodes. A third run's reject: no delta.
- `e2e/test_shutdown.py`: a model blocked mid-run, then shutdown: run ended, store closed, lock
  free; a venv build whose subprocess blocks on stdin: shutdown reaps it (its pid is gone) before
  the store closes; a `merge` waiting on an event: its `change.resolved` is in the closed log; the
  delta's publish failing during the decision: the drain sends it; a merge that leaves `MERGE_HEAD`
  and an abort that fails: `broken`, approve and start 409, reject 202, shutdown's `error` logged.

About 450 lines of code and 600 of tests, four commits under 1,000 lines each: startup and
shutdown, ledger and brake, routes, guard.

## Open, for the coordinator and the maintainer

1. **Stop**: AGENTS.md puts "stop and discard" in level 2 and the manager has no stop. 501 now.
2. **`internal` -> 500**: his answer picks the branch in section 2.
3. **Prices**: US$0.10 and 0.20 per million are the coordinator's recorded figures; to confirm.
4. **Manager additions** (section 4: `main_suspect`, `check_main`, the drain in `aclose`) go to
   the changes/manager front; this front waits for them.
5. **Files the first brief did not list**: root `conftest.py`, `.env.example`, `agents/usage.py`.
6. **Limits** in section 1, for the architect to confirm.
