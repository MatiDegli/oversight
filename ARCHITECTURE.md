# Architecture

This document describes main as of the end of day 1 (2026-10-26).
Contracts can describe operations that are not implemented yet.

## Overview

The backend is a Python 3.12 FastAPI application. Its composition root is
[app.py](backend/src/oversight/app.py). It wires the workspace, graph, event store, model
client and change manager together. Configuration comes from
[settings.py](backend/src/oversight/settings.py), including the environment and `.env`.

[Makefile](Makefile) provides the process entry points:

- `make dev` starts FastAPI with reload and the Vite development server.
- Vite serves the browser on port 5710 and proxies API routes to FastAPI, normally on 8710.
  The proxy streams SSE responses as they arrive. See [vite.config.ts](frontend/vite.config.ts).
- `make demo` builds the normal frontend, then starts FastAPI on `DEMO_PORT`, default 8710.
  FastAPI serves both the built UI and API on one origin. No Vite server runs in this mode.
- Starting the demo does not call a model. Starting a configured Meta agent does.

The [workspace package](backend/src/oversight/workspace) owns runtime files and git operations.
At live startup it takes a runtime lock, removes the previous workspace, worktrees and temporary
files, copies [demo-repo](demo-repo), and runs `git init` with an initial commit on `main`.
The copy lives at `.runtime/workspace/`. Agent worktrees live at
`.runtime/worktrees/{agent_id}`, outside the parsed main tree. These are product worktrees.
Run logs, the spending ledger and the separate demo test environment survive this reset.

## Data flow

1. Live startup resets the workspace and publishes `run.started`. The
   [graph package](backend/src/oversight/graph) scans Python files into module, class, function
   and method nodes, then resolves call edges. Node IDs derive from qualified names.
   File content hashes let later parses reuse unchanged files. A span index maps source lines
   to their innermost node. Startup publishes `graph.snapshot`, including saved layout positions.
2. `GET /graph` returns `run_id`, `seq` and the current main graph together. The
   [API routes](backend/src/oversight/api/routes.py) read this pair from the event store without
   an intervening await. The graph is folded from committed snapshots and deltas; `seq` is the
   last committed event, including events that do not alter the graph.
3. The browser sends `POST /agents`. The composition root checks the model, task, budget and
   single slot A, then opens an agent worktree through the change manager. The
   [agents package](backend/src/oversight/agents) runs `AgentRun` against a `ModelClient`.
   The live default uses the official OpenAI SDK at Meta's endpoint in Contributor mode.
   A scripted client supplies fixed replies in tests. The graph is not a model tool.
4. The loop offers `list_files`, `read_file`, `search`, `edit_file`, `run_tests` and `finish`.
   There is no shell tool. Reads are bounded; edits require a unique old-text match and the
   hash from the agent's previous read. Test execution uses the demo environment and a timeout.
   Tools validate paths against the worktree. Agent status follows tool execution.
5. Each completed tool call emits `agent.step`. Reads with source focus emit `agent.focus`
   before the step. A successful edit records `change.updated`, then edit focus, then the step.
   The change manager reparses the agent version and sends complete original and modified
   file contents. Usage replies produce `usage.tick`; the loop does not stream model text.
6. Producers publish to the [events package](backend/src/oversight/events). One writer assigns
   increasing `seq` values and timestamps, drains queued events into batched SQLite transactions,
   and exposes them only after commit. SQLite uses WAL and a busy timeout. Each live run gets
   an append-only database. Disk work runs in a thread.
7. `/events` streams committed envelopes over SSE with `id: seq`. Resume precedence is a
   different run ID resetting to zero, then `Last-Event-ID`, then the `after` query, then zero.
   The response disables cache transformation and sends keepalive comments when idle.
8. The [live transport](frontend/src/transport/live.ts) streams from seq 1 on a fresh store,
   opening `/events?after=0`. It adds the run ID when known and owns retries, closing failed
   streams and reconnecting with backoff and jitter from the last applied seq. A missing seq
   or graph-version mismatch also reconnects from that cursor. This departure from AGENTS.md
   was approved by the maintainer in [docs/proposal.md](docs/proposal.md), section 8, row 1:
   agent and change state exist only in events. `GET /graph` remains for the smoke test and
   debugging; the live transport does not read it first or fetch a snapshot on a gap.
9. Live and replay transports feed the same pure `applyEvent` reducer in
   [state](frontend/src/state). A vanilla Zustand store reduces event batches and exposes
   selectors. The reducer tracks the run, main graph, agents, steps, diffs, proposals and usage.
   The [graph view](frontend/src/graph) owns one Cytoscape instance and batches updates.
   The [app](frontend/src/app) selects watch, live diff or review panels. Agent cards in
   [agents](frontend/src/agents) show status and steps; [review](frontend/src/review) shows
   decisions and the cone. Local [Monaco integration](frontend/src/editor) displays inline,
   read-only original and modified models without needing the old worktree.
10. `finish` leads the [change manager](backend/src/oversight/changes) to commit the agent's
    changes, run tests and publish `change.proposed`. The proposal includes changed files and
    nodes, line counts, test results and a bounded impact cone on the agent version.
    The cone follows only exact call edges in reverse and reports truncation; possible edges
    are ignored. It means possibly affected.
11. Approve and Reject return HTTP 202 for an accepted decision. The result arrives through
    events. Approve takes main's lock and the agent lock, merges through the git CLI, rescans
    main, then publishes `change.resolved`, `graph.delta` and terminal `agent.status` in order.
    A failed merge can leave a conflict for review. Main-state uncertainty blocks new starts
    and approvals. Tests run at proposal time; this version does not test a combined merge.
12. Reject discards the agent worktree and resolves the change. Main is unchanged, so this
    implementation does not reparse main or publish a graph delta after discard. On
    `change.resolved`, [applyEvent](frontend/src/state/applyEvent.ts) keeps the change record and
    sets its status to approved, rejected, conflict or revising.
    [selectPendingChanges](frontend/src/state/selectors.ts) excludes only approved and rejected
    from pending changes. Shutdown waits for decisions and drains pending close-out events
    before closing SQLite and releasing the runtime lock.

```text
+----------------------------------+      +----------------------------------+
| LiveRun: app.py startup          |      | Event store: one writer          |
| run.started, graph.snapshot      |----->|                                  |
+----------------------------------+      | SQLite -> committed events       |
                                          |                                  |
+----------------------------------+      |                                  |
| AgentRun                         |      |                                  |
| status, step, focus, usage       |----->|                                  |
+----------------------------------+      |                                  |
                                          |                                  |
+----------------------------------+      |                                  |
| ChangeManager                    |      |                                  |
| changes, graph.delta, status     |----->|                                  |
+----------------------------------+      +----------------------------------+

Browser --HTTP--> FastAPI / LiveRun --runs--> AgentRun
AgentRun --change sink--> ChangeManager --git--> Workspace
LiveRun and ChangeManager --scan/cone--> Graph (publishes nothing)

Event store --SSE--> Browser -> pure reducer -> Zustand -> graph, cards and diff
Replay JSONL -> same reducer -> same views
```

## Backend module contracts

Cross-package imports use the exports of each package's `__init__.py`.
The import-linter rules live in the root [pyproject.toml](pyproject.toml).
`layers` orders `app -> api -> changes -> agents -> graph | events | workspace`.
The bottom three packages are mutually independent. Each package below has a
`private-<package>` protected contract that prevents imports of its internals by other packages.
`pure-contracts` keeps contracts independent of the backend and web/model frameworks.
`model-gate` allows only agents to import OpenAI directly. `web-at-the-edge` keeps FastAPI and
Starlette out of changes, agents, graph, events and workspace.

### Agents

[Public exports](backend/src/oversight/agents/__init__.py):
`AgentRun`, `AgentTask`, `RunEnd`, `Publish`, `ChangeSink`, `ModelClient`, `OpenAIClient`,
`Message`, `ModelReply`, `ModelAPIError`, `ToolCall`, `ToolSchema`, `Usage`, `RetryPolicy`,
`Script`, `ScriptedModel`, `load_script`, `parse_script`, `TOOL_NAMES`, `TOOL_SCHEMAS`,
`SYSTEM_PROMPT`, `REVISION_PROMPT`, `PROMPT_VERSION`, `META_BASE_URL`, `BASETEN_BASE_URL`,
`MUSE_SPARK`, `MUSE_SPARK_OPEN`, `FAKE_MODEL`, `UNVERIFIED_PROFILES`, `UnverifiedProfile`,
`Brake`, `Pricing`, `UsageLedger`, `UnknownUsage`, `LedgerError`, `LedgerFile`, `LedgerState`.
These cover the model boundary, tool loop, change sink and spending ledger.
Enforced by `private-agents`, `layers` and `web-at-the-edge`.
Profiles exported here do not all run in the live app: it accepts Muse Spark or fake only.

### API

[Public exports](backend/src/oversight/api/__init__.py): `Commands`, `ApiError`,
`create_router`, `add_static`, `error_response`, `install_error_handlers`.
`Commands` exposes start, approve and reject to the routes. The package owns HTTP error
mapping, SSE framing and static serving. Enforced by `private-api`, `layers` and `model-gate`.

### Changes

[Public exports](backend/src/oversight/changes/__init__.py): `ChangeManager`,
`ChangeNotFound`, `Revision`, `WrongState`. The manager implements the agents' change sink.
Its stage split (2026-10-27) keeps the same signatures, tasks and event ordering:

- `_state.ChangeState` exclusively owns changes, agents, bases, pending work, tracked tasks,
  suspect state and the published snapshot. Records and transitions live here without I/O.
- `_support.ChangeContext` holds dependencies and utilities, with a reference to that state.
- `_proposal` opens and versions worktrees, publishes live diffs and proposals and settles run end.
- `_approval`, `_merge` and `_recovery` retain the approval transaction, combined tests and
  fact-based recovery under the same locks. Recovery still makes main permanently suspect when
  its state cannot be established.
- `_delivery` owns close-out, receipts, retries, cleanup and shutdown. Its only facade callback,
  `ChangePorts.drain`, resolves the static `_drain` dynamically; the three delivery wait sites all
  use it. Other stage calls use direct imports.
- `_manager` keeps synchronous admission, file/save transactions, review edits, Request changes,
  Reject, Stop and main checks. Its assignable compatibility properties delegate to the owners;
  `request_changes` continues resolving `_request_changes` when called.

The move manifest and AST fidelity check are described in
[`_split.md`](backend/src/oversight/changes/_split.md); the residual file/revision inventory is in
[the change plan](docs/plans/changes.md#1-facade-and-callers). The original logger name is retained.
Existing tests and contracts are unchanged. `_policy`, `_zones` and `_beforeafter` remain unwired.
Enforced by `private-changes`, `layers`, `model-gate`, `web-at-the-edge` and the forbidden
`changes-no-facade-backedge` contract: helpers cannot import `_manager`, including typing imports.

### Events

[Public exports](backend/src/oversight/events/__init__.py): `EventStore`, `StoredEvent`,
`EventPayload`, `EVENT_TYPES`, `StoreClosedError`, `Clock`, `system_clock`, `format_ts`,
`parse_ts`, `LogError`, `read_jsonl`, `write_jsonl`, `export_run`, `read_store`, `play`,
`replay_delays`, `MAX_GAP_S`, `Sleep`, `run_store_path`.
The package owns persistence, committed subscriptions, graph folding and log playback.
Enforced by `private-events`, `layers`, `model-gate` and `web-at-the-edge`.

### Graph

[Public exports](backend/src/oversight/graph/__init__.py): `WorkspaceGraph`, `CodeVersion`,
`DeltaCause`, `NodeNotFoundError`, `parse_version`, `read_layout`, `graph_delta`,
`change_graph`, `impact_cone`, `change_cone`, `node_cone`.
These functions are synchronous and start no processes. Callers offload expensive parsing.
Enforced by `private-graph`, `layers`, `model-gate` and `web-at-the-edge`.

### Workspace

[Public exports](backend/src/oversight/workspace/__init__.py): `Workspace`, `Tree`, `Author`,
`FileRead`, `content_hash`, `ProcessResult`, `run_process`, `SuiteRun`, `WorkspaceError`,
`InvalidPath`, `StaleFile`, `UnsafeDeletion`, `WorkspaceBusy`, `LockOrderError`.
This package owns async subprocesses with timeouts, git, locks, safe paths and the demo test
suite. Enforced by `private-workspace`, `layers`, `model-gate` and `web-at-the-edge`.

### Settings and app

These are modules, not packages with their own `__init__` facades.
[settings.py](backend/src/oversight/settings.py) provides `Settings` and configuration helpers.
[app.py](backend/src/oversight/app.py) provides `create_app`, `ReplayConfig`, `LiveRun` and
application wiring; [__main__.py](backend/src/oversight/__main__.py) owns CLI entry points.
`layers` places app at the top; `model-gate` also covers settings and the CLI.
There is no separate protected-import contract for settings or app.

## Frontend module contracts

[eslint.config.js](frontend/eslint.config.js) requires cross-feature imports through `index.ts`,
including dynamic imports. Only transport may use `fetch` or `EventSource` globals.
Only graph may import Cytoscape/fcose; only editor may import Monaco packages.
State cannot import React or React DOM. These are ESLint rules, not import-linter contracts.

### State

[state/index.ts](frontend/src/state/index.ts) exports `applyEvent`, `createInitialState`,
`createStateStore`, `stateStore`, state/store types, `AgentCardData`, `LiveDiffData`, and selectors:
`selectSpend`, `selectAgentCard`, `selectLiveDiff`, `selectAgents`, `selectChangeForAgent`,
`selectHasReadyForReview`, `selectLitNodes`, `selectNodeMarks`, `selectPendingChanges`.

### Transport

[transport/index.ts](frontend/src/transport/index.ts) exports `startLive`, `startReplay`,
`startAppTransport`, `createCommandClient`, `CommandError`, `replayBuild`, `replaySource` and
`Transport`, `TransportStore`, `EventStream`, `LiveOptions`, `ReplayOptions`, `ReplaySource` types.
The command client includes contract routes that currently answer 501.

### Graph

[graph/index.ts](frontend/src/graph/index.ts) exports `GraphView`, `graphElements`,
`loadGraphEvents` and `GraphViewProps`, `GraphProbe`, `GraphProbeHost`, `NodeBounds`, `Framing`.

### App

[app/index.ts](frontend/src/app/index.ts) exports `App` and `startAppSession`.
It composes panels and owns the transport lifetime and local view selection.

### Agents

[agents/index.ts](frontend/src/agents/index.ts) exports `AgentCard`, `TaskBox`,
`AgentCardProps` and `TaskBoxProps`. These present agent progress and submit tasks.

### Review

[review/index.ts](frontend/src/review/index.ts) exports `ReviewPanel`, `ImpactConeCard`,
`decisionView`, `failureMessage`, `sendDecision`, `startAttempt`, `ReviewPanelProps`,
`DecisionAction`, `DecisionAttempt`, `DecisionClient`, `DecisionView`.
Accepted decisions stay pending until events or an error settle the attempt.

### Editor

[editor/index.ts](frontend/src/editor/index.ts) exports `EditorDiff`, `LiveDiff`,
`createEditorTheme`, `LiveDiffProps` and `EditorColors`. Monaco and its worker are bundled locally.
The current diff stays read-only in both live diff and review modes.

### Theme

[theme/index.ts](frontend/src/theme/index.ts) exports `themes` and `getActiveTheme`.
[theme/index.css](frontend/src/theme/index.css) supplies Tailwind, fonts and root theme tokens.
Graph and editor receive their palette through this shared theme interface.

## Wire contracts

Pydantic models in [contracts/src/oversight_contracts](contracts/src/oversight_contracts) are the
source. [generate.py](contracts/generate.py) generates
[oversight.schema.json](contracts/generated/oversight.schema.json) and
[contracts.ts](contracts/generated/contracts.ts) with json-schema-to-typescript.
TypeScript and the generator are pinned in [package.json](frontend/package.json).
`make check-contracts` regenerates in memory and compares both outputs byte for byte.
[CI](.github/workflows/ci.yml) runs this through `make check-ci`.

[events.py](contracts/src/oversight_contracts/events.py) defines the event union:
`run.started`, `graph.snapshot`, `graph.delta`, `agent.status`, `agent.step`, `agent.focus`,
`change.updated`, `change.proposed`, `change.resolved`, `change.assessed`, `merge_mode.changed`,
`plan.updated`, `usage.tick`, `error`.
Every envelope has `seq`, `ts`, `type` and `payload`. Graph payload models live in
[graph.py](contracts/src/oversight_contracts/graph.py).

[commands.py](contracts/src/oversight_contracts/commands.py) defines request/response bodies.
[Command routes](backend/src/oversight/api/commands.py) implement `POST /agents`,
`POST /changes/{change_id}/approve` and `POST /changes/{change_id}/reject` live.
`GET /graph`, `GET /health` and `GET /events` are in the API routes cited above.
The remaining contract routes return 501: `POST /agents/{agent_id}/stop`,
`POST /changes/{change_id}/request-changes`, `GET /impact`, `GET /files`, `PUT /files`,
`POST /graph/rescan`, and `POST /agents/{agent_id}/message`, `/pause`, `/resume`,
`GET /merge-mode`, `PUT /merge-mode`, `POST /plans`, `GET /plans/{plan_id}`,
`PUT /plans/{plan_id}`, `POST /plans/{plan_id}/start`, `POST /plans/{plan_id}/reject`.
Backend replay mode also refuses start, approve and reject with 501.

## Testing and gates

[The fake model](backend/src/oversight/agents/fake.py) returns scripted tool calls without
provider credits. [conftest.py](conftest.py) strips provider keys and blocks non-loopback HTTP
transports in tests. [The fixture schema test](backend/tests/events/test_fixtures.py) validates
every event-log line against Pydantic `ServerEvent`, checks seq order and rejects absolute paths.
Generated TypeScript types do not validate payloads at runtime; the browser checks envelopes.

`make check` runs four groups in parallel: Python, contracts, frontend and secrets.
Python runs Ruff lint/format, Pyright, import-linter and pytest. Frontend runs strict TypeScript,
ESLint, Prettier, unit tests and the boundary regression test. Secrets runs gitleaks.
CI runs the same groups through `check-ci`, with gitleaks in its own job, then `make smoke`.
Browser tests run separately through `make test-browser`; they are outside `make check` and CI.

`make demo-check` builds the UI and runs [demo_check.py](tools/demo_check.py).
It starts the real server three times with the fake model, verifies clean graphs and static
serving, starts an agent, approves its proposal and checks resolution and graph delta events.
It also checks shutdown, subprocess cleanup and release of the runtime lock. It is outside check.

`make replay` plays JSONL through the backend event store and SSE with Vite serving the UI.
For a static site, `pnpm -C frontend build --mode replay` bundles
[handwritten-level1.jsonl](fixtures/events/handwritten-level1.jsonl) without a backend.
The transport labels it `Replay · hand-written demo fixture`. It is not a real recorded run.
The static replay build is manual and not gated yet: no make target, check group or CI step
builds with `--mode replay`.

## In progress

These level 2 flows are not on main. The existing plans describe extension points, not delivery.

- Request changes: [plan](docs/plans/changes.md#6-level-2-kept-out-but-not-blocked);
  the loop has revision support, but the HTTP route still returns 501.
- Two agents in parallel: [plan](docs/plans/changes.md#6-level-2-kept-out-but-not-blocked);
  the live application still admits only slot A.
- File editing: [plan](docs/plans/changes.md#6-level-2-kept-out-but-not-blocked)
  and [API plan](docs/plans/api.md); file routes return 501 and the review diff is read-only.
