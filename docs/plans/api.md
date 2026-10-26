# Plan: the API and the backend wiring, level 1 (front 16)

Sources: AGENTS.md, docs/proposal.md (3, 4.5 to 4.7, 5, 7), `contracts/`, main at 479b3ec, and the
change manager's facade on `changes/manager` f0225e4 (`ChangeManager`, `ChangeNotFound`,
`WrongState`; `open`, `run_ended`, `approve`, `reject`, `aclose`). No contract change, no new
dependency, no new backend module: the starter lives in `app`, the routes in `api`.

## 1. Startup and shutdown (`app.py`, `settings.py`, `__main__.py`, `tools/smoke.py`)

**Today** `Workspace.reset` copies `demo-repo/` to `.runtime/workspace/`, runs `git init -b main`
and makes the fixed initial commit; `acquire_runtime`, `ensure_demo_venv`, `EventStore`,
`WorkspaceGraph(root, layout)` and `app.state.layout` exist. Nothing calls them: the live app has no
lifespan, store or routes, and `make smoke` runs replay. **Settings** gains `OVERSIGHT_*` values for
`agent_model` (`muse-spark`, or `fake` with `fake_script`), `budget_usd`, optional `Pricing`, the
`Limits`, `pytest_pin` and `git_timeout_s`; and `demo_python`, with no variable, for tests only.

`create_app` builds the `Workspace`, the run id (`run-YYYYMMDD-HHMMSS`, next second if that log
exists) and its `EventStore`; the live lifespan then runs, in order:

1. `acquire_runtime()` (a second server fails to start with `WorkspaceBusy`), `base = reset()`.
2. `store.start()`; publish `run.started`: `PROMPT_VERSION`, `base_commit`, the configured profile,
   slots `[A]`, the limits, `Budget(budget_usd, 0.0, [0.5, 0.8], 0.9)`.
3. `WorkspaceGraph(main.root, app.state.layout).scan(base)` in a thread; publish `graph.snapshot`.
4. `ChangeManager(workspace, publish, main_graph=..., main_snapshot=..., git_timeout_s=...)`, the
   runtime's sink; `publish` is `store.publish` wrapped to watch `change.resolved` (section 2).
5. The demo venv build starts as a background task; `POST /agents` awaits it. A failed build is an
   `error` (source `tests`, not fatal); the run goes on and its `run_tests` reports `error`.

**Shutdown**, in the lifespan's `finally`, each step even if the one before failed: cancel the run
task and await it (tools and `run_process` kill their process groups on cancellation), the
starter's `finally` calling `run_ended(agent, None)`; cancel the venv task; `await
manager.aclose()`, which waits for decisions in flight, each git call bounded by `git_timeout_s`;
`store.close()` last, so every event a decision sends commits and SSE streams end;
`release_runtime()`. `serve` gets `timeout_graceful_shutdown=1`, as replay has, so an open tab's
stream cannot hold shutdown. `tools/smoke.py` switches to `("serve",)` with the fake model.

## 2. Routes for level 1 (`api/routes.py`, new `api/commands.py`, `api/__init__.py`)

`create_router(store, mode, commands=None)`: replay answers 501 on commands; live passes the manager
and a `Starter` protocol that `api` defines and `app` implements, as with `ChangeSink`, since
starting needs `workspace`. Errors go through `ApiError` and `ERROR_STATUS`: `{"error": {...}}`.

| Route | Success | Errors |
| --- | --- | --- |
| `POST /agents` `StartAgent` | 201 `AgentCreated` once `open` returned | `spend_brake` 403, `slot_busy` 409, `invalid_request` 422 |
| `POST /changes/{id}/approve` `{comment?}` | 202, empty; results as events | `not_found` 404, `wrong_state` 409 |
| `POST /changes/{id}/reject` `{comment?}` | 202, empty | `not_found` 404, `wrong_state` 409 |
| `GET /graph`, `/events`, `/health` | as today, `mode="live"` | as today |
| `POST /agents/{id}/stop` | 501 `not_implemented` at level 1 (Open 1) | |
| request-changes, `GET /impact`, `GET`/`PUT /files`, `POST /graph/rescan`, message, pause, resume | 501 `not_implemented`, body not parsed | |

- **Start.** Brake first: for a profile whose provider is not `fake`, `ledger.brake == "blocked"`
  (above 90%, or no budget set: it fails closed) is `spend_brake`. 50% and 80% do not refuse: the
  runtime's `usage.tick` carries `warn_50`/`warn_80` for the UI. Then the slot: one agent at a time,
  slot `A` only (`B` is `invalid_request`). The starter claims it with no `await` between check and
  claim, so a racing start gets `slot_busy`, and holds it until a `change.resolved` with `rejected`
  or `merged` passes the publish wrapper. Then ids `agent-n`/`change-n`; the client (section 4); a
  profile in `UNVERIFIED_PROFILES` (Muse Spark Open) is `invalid_request` before anything opens;
  the venv task; `tree = await manager.open(task, profile.id)`; then `AgentRun(..., tree=tree,
  sink=manager)` in the run task, with `run_ended` in its `finally`. If `open` raises, the slot is
  released, an `error` is published and the answer is a 500 (Open 2).
- **Decisions.** `manager.approve(id, comment)` or `reject`: `ChangeNotFound` is `not_found` (a
  malformed id too: path ids are plain strings), `WrongState` is `wrong_state` with the manager's
  message, otherwise 202. A missing body is no comment. The manager tracks the returned task.

## 3. Concurrency

- **A second Approve, or a Reject, while a decision runs**: `_take` flips the change to `deciding`
  with no `await`, so the second call raises `WrongState` before any git call: 409 at once. Also on
  `closing` and `closed`. `broken` refuses Approve and accepts Reject. **Stop** during a decision
  finds no run (a decision needs `run_ended`): `wrong_state` once stop exists, 501 at level 1.
- **Shutdown mid-merge**: `aclose` waits for the decision, which ends merged, `ready` after `merge
  --abort`, or `broken`, and sends its events before the store closes. A forced exit (a second
  Ctrl+C) can leave main half-merged: the next start's `reset` rebuilds it from the template, and
  the old log ends without `change.resolved`; the new `run.started` resets the UI.

## 4. Models, and credits in tests

`muse-spark` builds `OpenAIClient(MUSE_SPARK, base_url=META_BASE_URL, api_key=meta_api_key,
max_output_tokens=limits.max_output_tokens)`: model `muse-spark-1.3-contributor`, reasoning `low`
from the profile on every call. `StartAgent.model` may name only the configured profile. No key:
`invalid_request`, before any client exists. `fake` builds `ScriptedModel(load_script(...))`, no
credits. Tests never call `Settings.load()`; they build `Settings(agent_model="fake", ...)`. An
autouse fixture in the api and e2e conftests strips `META_API_KEY` and `BASETEN_API_KEY` and
replaces the name `app` builds the real client with, so a test that would reach Meta fails instead.

## 5. What the frontend may rely on (front 17)

- `POST /changes/{change_id}/approve|reject` with `{}` or `{comment}`; `change_id` comes from
  events (`agent.status`, `change.proposed`), never only from the `POST /agents` answer.
- 202 means accepted, not merged; state changes only through events: `change.resolved`, then
  `graph.delta` (cause `merge`), then `agent.status` `done`. Reject sends no delta.
- Both buttons stay disabled from the click until `change.resolved`, a 4xx, or an `error` naming
  the change: a merge that failed with main unchanged sends that `error` and no resolution.
- Do not assume: a delta with nodes (a body-only edit gives an empty one); `merge == "merged"`
  (level 2 has `conflict`); slot `B`; a fixed agent id; working Request changes or Stop (501); an
  approvable halted change with no proposal (Reject only). No key approves.

## 6. Tests: `uv run pytest backend/tests/api backend/tests/e2e`, then `make smoke`

A real uvicorn server on a free port, as `test_routes.py` does; `asyncio.timeout(60)`, waiting on
events, never sleeping; every envelope validated against `ServerEvent`; runtime in `tmp_path`.

- `api/test_commands.py`: unknown and malformed ids are 404; approve before the proposal is 409; a
  second approve, or a reject, while the first decides is 409 and one `change.resolved` goes; a
  racing `POST /agents` is `slot_busy`; a spent ledger or no budget is `spend_brake`, never for the
  fake profile; slot `B`, an empty task, Muse Spark Open and a missing key are 422; each 501 route
  answers `not_implemented`, and replay does on commands.
- `test_app.py`: the no-routes test becomes: startup publishes `run.started`, then `graph.snapshot`
  with the layout's positions, and `GET /graph` answers at seq 2.
- `e2e/test_level1.py`, a fake script that reads `shop`, adds a function, runs tests and finishes:
  start (201), `change.proposed`, approve (202), then `change.resolved`, `graph.delta` with the new
  node and `from_version` equal to the snapshot's, `agent.status` `done`, in that order on
  `/events`; `GET /graph` holds the node. A second run's reject sends no delta.
- `e2e/test_shutdown.py`: a fake model blocked on an event mid-run, then shutdown: the run ends,
  the store closes, the runtime lock is free. A `Workspace` whose `merge` waits on an event:
  shutdown waits for the decision, and its `change.resolved` is in the closed log.

About 300 lines of code and 400 of tests, in two commits: startup and shutdown, then the routes.

## Open, for the coordinator and the maintainer

1. **Stop.** The brief puts it in level 1; AGENTS.md and proposal 5 put "stop and discard" in level
   2, and the manager has no stop: `reject` cannot send reason `stopped` and ends in `done`, not
   `stopped` (4.5). Proposed: 501 now; at level 2 the manager adds `stop(agent_id)`.
2. **Contract gap: no 5xx code.** A server-side failure (`open` failing, an unexpected error) has
   no `ErrorCode`, so FastAPI's plain 500 leaves the single format. Proposal: `internal` → 500.
3. **The brake is inert on real runs.** No Muse Spark price is known, so every cost is unknown and
   the ledger never moves; no ledger is persisted, so `spent_usd` before the run is 0 and a restart
   resets it. Needed: prices and a budget, and a `.runtime/ledger.json` (level 2).
4. **Values to confirm**: `pytest_pin` (no pin exists in the repository), and default limits:
   30 steps, 4096 output tokens, 600 s, tests 60 s, cone depth 4 and 40 nodes.
