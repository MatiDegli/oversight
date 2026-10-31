# Architecture

This document describes main at the level 2 close on Tue Oct 27, 2026,
and the current validation tooling.

## Startup and serving

[app.py](backend/src/oversight/app.py) composes the Python 3.12 FastAPI backend.
[settings.py](backend/src/oversight/settings.py) reads configuration from the environment
and `.env`. [setup.py](backend/src/oversight/setup.py) checks the offered slots' setup
without calling a provider. Health reports missing settings without exposing their values.

[Makefile](Makefile) provides the entry points:

- `make dev` starts FastAPI with reload and Vite. Both processes receive resolved `OVERSIGHT_PORT`
  (8710) and `OVERSIGHT_VITE_PORT` (launcher fallback 5710), with environment precedence over `.env`.
  Vite uses strictPort and proxies API routes while preserving Host and Origin.
  The SSE proxy streams without buffering.
- `make demo` checks setup, builds the frontend and serves it with the API on one origin.
  `DEMO_PORT` defaults to 8710; demo explicitly clears `OVERSIGHT_VITE_PORT`, overriding `.env`.
  Starting the server spends no model credits.
- `make replay` plays JSONL through the event store and SSE, with Vite serving the UI.

[The API guard](backend/src/oversight/api/security.py), exported by the API facade and installed by
`create_app`, checks every HTTP request and WebSocket handshake in live and server replay before
routing. Its single policy admits only `localhost`, `127.0.0.1`, `[::1]` at P and optional V, and
exact `http://` Origins at those authorities. Missing/duplicate/malformed Host and supplied invalid,
null, duplicate or foreign Origin receive typed `forbidden/403` without echoing header values.
Absent Origin is allowed for HTTP CLI writes with the terminal off; Host is still checked. No CORS
or forwarded headers widen admission. Uvicorn disables proxy-header interpretation so the
transport scheme, used for omitted Host ports, cannot be changed by forwarded headers. Omitted authority ports mean protocol defaults. Accepted IPv6
authorities do not change listener binding. WebSockets are refused before acceptance (HTTP denial
if supported, otherwise close). Lifespan and other scopes pass through; SSE is never buffered.

The backend's V defaults to unset; empty V is unset, invalid ports or P=V stop startup. The shared
settings parser also resolves launcher exports. `make shots` exports its distinct free P/V and
checks readiness through Vite's `/graph`. `make test-browser`, under a timeout and `tools/capped.sh`,
exercises actual dev/replay launchers, environment and port-only `.env` overrides, `/graph` and
streaming/resumed `/events`, with its own runtime/cache. Defaults are checked from resolved exports
without binding 8710/5710. CI invokes that target. Future terminal controls layer same-authority,
authentication and IPv4 listener requirements on this policy; see approved guard decisions G1–G3.

At live startup, the workspace takes a runtime lock, removes the previous workspace and
worktrees, copies [demo-repo](demo-repo) and creates an initial git commit on `main`.
Main lives at `.runtime/workspace/`; product agent worktrees live at
`.runtime/worktrees/{agent_id}`, outside the parsed main tree. Run logs, the spend ledger
and the separate demo test environment survive this reset.

## Data flow

1. `LiveRun` publishes `run.started` with model profiles, slots, prompt version, limits and
   budget. It scans main and publishes `graph.snapshot` with the saved demo positions.
2. `POST /agents` checks the task, model, setup, spend brake and slot availability.
   Slots A and B can run in parallel, each with its own worktree and model client.
   A slot stays held until its change is merged or rejected, including Stop/discard.
3. `AgentRun` drives `ModelClient`. Muse Spark uses the official OpenAI SDK at Meta's
   endpoint in Contributor mode. Muse Spark Open uses the same interface at Baseten's
   endpoint. Each slot has its own configured profile. Tests use scripted fake clients;
   fake B needs its own script. `LiveRun` registers the drive's `RunControl` before returning
   Start and before the first model call; a revision installs a fresh control sharing the
   lifetime message budget. Active registrations are dropped when a drive ends, on Stop and
   during shutdown. Closed controls cannot restart a paused gate.
4. The loop offers `list_files`, `read_file`, `search`, `edit_file`, `run_tests` and `finish`.
   There is no shell tool. Paths stay within the worktree. Reads are bounded. An edit needs
   a unique old-text match and the hash from the agent's last read. Tests use the demo's
   own environment and a fixed command with a timeout. Status follows tool execution and
   human run controls, never model text.
5. Every completed tool call emits `agent.step`. Read focus precedes the step. Successful
   edits publish `change.updated`, edit focus and then the step. Updates carry full original
   and modified file contents, author and a pending graph overlay. Model replies produce
   `usage.tick`; the loop does not stream model text.
6. The event store assigns increasing `seq` values and timestamps. One writer batches
   SQLite transactions and exposes events only after commit. SQLite uses WAL and a busy
   timeout. Each run has its own append-only database. Disk work runs in a thread.
7. `/events` streams committed envelopes over SSE with `id: seq`. Another run ID resets
   the cursor to zero; otherwise resume uses `Last-Event-ID`, then `after`, then zero.
   The response disables cache transformation and sends idle keepalive comments.
8. The browser starts at `/events?after=0`. It reconnects from the last applied seq, with
   backoff and jitter, after disconnects, sequence gaps or graph-version mismatches.
   It does not fetch a snapshot first or on a gap. This approved departure is recorded in
   [docs/proposal.md](docs/proposal.md), section 8, row 1: agent and change state need events.
9. Live and replay feed the same pure `applyEvent` reducer in Zustand. It folds main's
   snapshots and deltas and keeps agent state, steps, diffs, proposals and usage separately.
   Pending graphs overlay main visually. Events update the open view without opening review
   or taking focus. A finished agent shows Ready for review.
10. `finish` commits the worktree, runs tests and publishes `change.proposed`. The proposal
    contains files, nodes, line counts, tests and an impact cone on the agent's version.
    The cone follows exact call edges in reverse. It reports depth and node-count omissions
    and means possibly affected. Possible edges are not followed.
11. Approve, Reject, Request changes and Stop return HTTP 202 for accepted commands.
    Their results arrive as events. Approve holds main's lock and then the agent's lock.
    It checks that the worktree matches the clicked revision before merging through git.
    If main moved since the agent's base, it tests the staged combination before committing.
    Merge conflicts or failed combined tests abort the merge and leave the change in conflict.
12. A successful approval rescans main and publishes `change.resolved`, `graph.delta` and
    terminal `agent.status` in order. Reject discards the worktree. Stop cancels and waits
    for a working agent, then discards it. Stop emits stopped status and a rejected resolution
    with reason stopped. Discard leaves main unchanged, so it emits no main graph delta.
13. Request changes requires a comment. It keeps the worktree and resumes the same agent run
    with that comment. The resolution records changes_requested and agent status becomes
    revising. The next proposal has a higher revision on the same change. From conflict,
    the manager first merges current main into the agent branch for the agent to resolve.
14. `GET /files?path=...` reads main; adding `agent` reads a worktree. Both return a hash.
    `PUT /files` with `agent_id` and `base_hash` saves the review's modified side only while
    ready. It commits as the user, reparses, tests and re-proposes with user attribution.
    A stale hash fails. The agent must re-read a user-edited file before editing it again.
15. Without `agent_id`, `PUT /files` commits an existing file on main as the user and publishes
    a user_edit `graph.delta`. It runs no tests. A later approval tests the combination if
    main moved. `POST /graph/rescan` rescans main and emits a delta when it changed.
    Suspect, dirty or mid-merge main cannot take these writes or rescans.

`GET /graph` returns `run_id`, main's graph and the last committed event seq together,
without an intervening await. The event store folds the graph from committed snapshots and
deltas; seq includes events that do not change the graph. The endpoint serves smoke checks
and debugging. The live frontend uses the event stream instead.

Unknown main state blocks new starts, revisions and approvals until restart. Shutdown waits
for decisions, drains pending close-out events, checks main, closes SQLite and releases the
runtime lock. Rejected and approved change records remain in frontend state for inspection,
but stop contributing pending marks.

## Backend module contracts

Cross-package imports use each package's `__init__.py`. The import-linter rules in
[pyproject.toml](pyproject.toml) enforce `app -> api -> changes -> agents -> graph | events |
workspace`. The bottom three packages are independent. Protected contracts keep package
internals private. Contracts import neither the backend nor web/model frameworks. Only agents
may import OpenAI; only app and API may import FastAPI or Starlette.

- [API](backend/src/oversight/api/__init__.py) exports `Commands`, route construction, error
  mapping and static serving. `Commands` covers start, stop, approve, reject, request_changes,
  get_file, save_file and rescan. The package owns HTTP responses and SSE framing.
- [Agents](backend/src/oversight/agents/__init__.py) exports the loop, model interface, SDK
  client, scripted fake client, tool schemas, profiles and usage ledger. Provider calls set
  reasoning explicitly. Retries use backoff and jitter for 429 and 5xx. Unverified profiles
  are refused. The same call with the same result four times, or six consecutive tool
  failures, halts the run.
- [Graph](backend/src/oversight/graph/__init__.py) exports `WorkspaceGraph`, version parsing,
  graph deltas, change graphs, layout loading and cones. `WorkspaceGraph.node_at` returns
  the innermost node at a source line.
  It publishes no events and starts no processes. Callers offload expensive parsing.
  File hashes reuse unchanged parses; no file watcher drives updates. IDs use qualified
  names rather than lines. The cone bridges constructors to their classes, walks through
  tests and counts reaching test functions without listing tests as entries.
- [Events](backend/src/oversight/events/__init__.py) exports the store, committed subscriptions,
  clock helpers, JSONL import/export and playback. It also folds main's published graph.
- [Workspace](backend/src/oversight/workspace/__init__.py) owns safe paths, hashes, async git
  and test subprocesses with timeouts, locks, worktrees and the demo test environment.
  These product worktrees are separate from worktrees used to develop Oversight.
- [Settings](backend/src/oversight/settings.py), [setup](backend/src/oversight/setup.py) and
  [app](backend/src/oversight/app.py) own configuration, readiness and composition.
  [The CLI](backend/src/oversight/__main__.py) owns serve, replay and ledger commands.

### Change manager stages

[changes/__init__.py](backend/src/oversight/changes/__init__.py) exports only `ChangeManager`,
`ChangeNotFound`, `Revision` and `WrongState`. The manager implements the agents' change sink.
Its facade delegates to private stages:

- `_state.ChangeState` owns records, bases, pending delivery, tasks, suspect state and the
  published snapshot. Its transitions do no I/O. `_support.ChangeContext` holds dependencies
  and utilities with a reference to that state.
- `_proposal` opens and versions worktrees, publishes live diffs and proposals and settles
  run end. `_diff` counts file changes; `_versions` parses versions and builds deltas.
- `_approval`, `_merge` and `_recovery` own revision checks, merge transactions, combined tests
  and recovery from git facts under the same locks. Unknown main state stays suspect.
- `_delivery` owns close-out, receipts, retries, cleanup and shutdown. Its `ChangePorts.drain`
  callback resolves the facade's static `_drain` dynamically at its three wait sites.
  Other stage calls use direct imports.
- `_files` owns file reads, review saves, main saves, rescans and main checks.
  `_revision` owns Request changes and moves to a new base.
- `_manager` retains admission, Reject, Stop and forwarding. Compatibility properties delegate
  to state. `request_changes` dynamically calls `_request_changes`.

`changes-no-facade-backedge` prevents helpers from importing `_manager`, including typing imports.
The [stage plan](docs/plans/changes.md#1-facade-and-callers) records the split and fidelity checks.

### Usage and the spend brake

[usage.py](backend/src/oversight/agents/usage.py) owns pricing, the in-memory usage ledger
and the durable ledger file. `usage.tick` carries tokens, cost, spend, the budget and
brake state. Missing usage or pricing stays unknown, never zero. The frontend shows total
spend and each agent's cost from these events.

The app records Meta costs before publishing their ticks. The durable ledger survives resets.
Unknown costs block further Meta starts and revisions until settled. A ledger write failure
halts the affected run and blocks Meta starts. Warnings begin at 50% and 80%; spend above 90%
refuses new Meta runs and revisions. It does not cancel other runs already working.
Baseten and fake calls do not debit Meta's credit ledger.

## Frontend module contracts

[eslint.config.js](frontend/eslint.config.js) requires cross-feature imports through `index.ts`,
including dynamic imports. Only transport may use `fetch` or `EventSource`. Only graph imports
Cytoscape/fcose; only editor imports Monaco. State cannot import React or React DOM.

- [State](frontend/src/state/index.ts) exports the pure reducer, Zustand store, selectors and
  spend helpers. Main's graph and pending change graphs stay separate.
- [Transport](frontend/src/transport/index.ts) owns live SSE, replay and command requests.
  It checks event envelopes; generated TypeScript types do not validate payloads at runtime.
- [App](frontend/src/app/index.ts) composes panels, owns transport lifetime and local view
  selection, and keeps file drafts and saves across panel changes.
- [Agents](frontend/src/agents/index.ts) presents both slots, status, steps, per-agent spend,
  task entry and Stop and discard.
- [Review](frontend/src/review/index.ts) presents proposals, impact and the three decisions.
  Accepted decisions stay pending until an event or error settles them. Unsaved review edits
  block Approve and Request changes until saved or discarded. Approve has no keyboard shortcut.
- [Editor](frontend/src/editor/index.ts) exports `LiveDiff`, `EditorDiff`, `ReviewEditor`,
  `FileView` and file helpers. Monaco and its worker are bundled locally. Fonts load before
  creation. The live diff and original side stay read-only; the modified side is editable
  in ready review. File view saves to main. Replay sends no edits or decisions.
- [Theme](frontend/src/theme/index.ts) and [theme CSS](frontend/src/theme/index.css) supply
  fonts, Tailwind and tokens selected through root `data-theme`. Graph, editor and dot field
  receive their colors through the shared theme interface.

### Graph folding and camera

[GraphView](frontend/src/graph/GraphView.tsx) owns one Cytoscape instance.
`graphElements` overlays pending additions and agent marks on main without changing state's
main graph. Pending borders are dashed in the agent's color. Review dims nodes outside its
change and cone. Live diff derives an exact-call cone from the current overlay; review uses
the proposal's cone. Tests are hidden in the graph by default.

`updateGraph` batches updates by ID and retains saved or dragged positions. Modules and classes
are compound frames. New nodes use free space; if a frame cannot grow, only that root moves.
The initial view uses the demo preset, with fcose as fallback.

Opening a left panel folds the agents panel into a rail and moves the camera without running
a layout. Review saves the watch camera and frames the change and cone down to a readable zoom
floor. Closing restores it. Cone rows report out-of-view nodes and reveal them on click.
Incoming review events do not reframe. In watch, new nodes can expand the view after a navigation
quiet period. Zoom controls move the camera, never nodes.

### Dot field

[The dot field](frontend/src/graph/field) is a separate WebGL canvas below transparent Cytoscape.
It reads rendered node boxes and the app's watch, live diff, review or file scope. It takes no
pointer or focus. Colors and motion settings come from theme tokens. Animation pauses for hidden
tabs and reduced motion. Missing WebGL, draw failures or context loss leave plain black ground.
It adds no server events.

## Wire contracts and routes

Pydantic models in [contracts/src/oversight_contracts](contracts/src/oversight_contracts) are the
source. [generate.py](contracts/generate.py) generates JSON Schema and TypeScript with pinned
json-schema-to-typescript. `make check-contracts` compares both generated outputs byte for byte.

Live events are `run.started`, `graph.snapshot`, `graph.delta`, `agent.status`, `agent.step`,
`agent.message`, `agent.focus`, `change.updated`, `change.proposed`, `change.resolved`, `usage.tick` and `error`.
Every envelope has `seq`, `ts`, `type` and `payload`.
`agent.message` records accepted human text, masked before storage and withheld by default in exports.

Live routes implement `POST /agents`, `POST /agents/{agent_id}/stop`,
`POST /agents/{agent_id}/message`, `POST /agents/{agent_id}/pause`,
`POST /agents/{agent_id}/resume`,
`POST /changes/{change_id}/approve`, `POST /changes/{change_id}/reject`,
`POST /changes/{change_id}/request-changes`, `POST /grants/{grant_id}/revoke`, `GET /files`,
`PUT /files` and `POST /graph/rescan`.
`GET /graph`, `GET /health` and `GET /events` provide graph, readiness and events.

| Read route (live and replay) | Response |
| --- | --- |
| `GET /agents/{agent_id}` | 200 latest committed coding `AgentStatus`; 404 `not_found` otherwise. |
| `GET /changes/{change_id}` | 200 archived `ChangeView` at one committed seq, with usage and confirmed revision rounds (null when unavailable); 404 `not_found` otherwise. |

Message, Pause and Resume accept only human HTTP commands and answer an empty 202.
The manager's public eligibility check admits only working changes; the drive also refuses
controls once proposal or halt finalization begins. Preparation/decisions, review, conflicts,
broken and closed changes return 409 `wrong_state`; unknown agents return 404 `not_found`.
All three routes answer 501 in replay before reading the body. No lead or agent tool exposes
these controls, and there is no frontend control in this package.

Messages queue in arrival order until the next safe model-call boundary. Text lives only in
memory (the queue and retained conversation); no event or SSE envelope carries it. Admission
refuses blank text, more than 4,096 characters, more than 32 queued messages, or more than
8,192 accepted UTF-8 bytes over the agent's lifetime with 422 `invalid_request`. Delivery,
closing and revision never replenish that lifetime budget.

Pause takes effect at a run-side gate after an in-flight call/report completes. Only actual
paused time extends the wall-clock deadline. Resume also cancels a pending pause; before
mutating either kind of pause it rechecks setup (503), the provider's spending brake (403)
and suspect main (409). Refusal preserves the pause. The restored status is working or
revising as appropriate. While paused the manager remains working, retaining the slot and
worktree, so review decisions and review writes remain refused. The wait holds no git lock;
decisions retain main-then-worktree lock order. Stop closes the control and cancels the wait,
then follows the existing discard path: stopped status before the rejected resolution.

`GET /impact` and plan routes still return 501. Merge-mode GET and PUT are live; before F10,
PUT merge_when_safe returns 409. Merge-mode routes return 501 only in a replay.
The 1.4 reservations also include tree, history, queue, leads, and change
review. `GET /tree?agent=` is shape-only: no traversal or hashing runs yet.
The Revert route answers 202 after synchronous admission, loads its target from this run's
committed log, and reports its outcome only through events.
Five more event types are reserved: change.reviewed, change.facts_recorded, tests.executed,
queue.updated and lead.status. Transport and reducer consume their sequences.
Approve, Reject and Request changes require the revision and head shown by the review render;
revision zero is limited to rejecting the published agent base before any proposal.
Live resolutions record principal and head explicitly. The writer validates each frozen
candidate independently before SQL admission. EventRejected is terminal for that delivery;
valid neighbors still commit with contiguous sequences. A rejected post-effect resolution
retains its Git facts, evidence, worktree and slot until a clean restart. Replay stores are
configured explicitly; accepting fake reviews additionally requires explicit server permission.
Proposal cones and the frontend's live preview work without `GET /impact`.
Backend replay returns 501 for all command routes, including file reads and rescans.

## Testing and replay

[conftest.py](conftest.py) strips provider keys and blocks non-loopback HTTP in tests.
Scripted fake models spend no credits. Fixture tests validate every recorded envelope against
Pydantic `ServerEvent`, check seq order and reject absolute paths.

`make check` runs Python, contracts, frontend and secrets groups in parallel. It runs Ruff,
Pyright, import-linter, pytest, strict TypeScript, ESLint, Prettier, Vitest, boundary tests and
gitleaks. CI runs `check-ci` with gitleaks in its own job, then smoke checks. The browser job
runs `make e2e` over the static replay build and Monaco/graph browser tests. Browser tests and
e2e are outside `make check`.

`make demo-check` builds the UI and starts the real server three times with two scripted fake
agents from clean workspaces. It checks concurrent steps, pending overlays, impact, usage ticks,
review saves and attribution, stale edits, Request changes and higher revisions, Stop/discard,
approval and main file edits. It also checks static serving, shutdown, subprocess cleanup and
release of the runtime lock. It is outside `make check`.

The static build, `pnpm -C frontend build --mode replay`, needs no backend. It bundles a verified
real recording when present, otherwise a labeled fixture. `make record` exports a finished real
run with a provenance sidecar. The build checks provenance, contracts and secrets. No real run
is bundled yet. `make e2e` runs two Playwright tests over level 1 and scripted level 2 replays.

## Private level 3 cores

Main contains `changes/_policy`, `_zones`, `_runrule`, `_beforeafter`, `_reviewfacts` and
`agents/_crossreview`. They cover merge rules, plan zones, test-run evidence, before/after
results, review facts and cross-review parsing. They are private and not wired into the live
flow. Reserved `change.assessed` and `plan.updated` events only advance the frontend sequence;
`merge_mode.changed` sets the merge mode that the status line and Settings show.
Contracts are at 1.4.0; delivery A was approved by the maintainer, 2026-10-27.
Level 3 wiring starts after this level 2 close.
