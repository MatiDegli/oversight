# Architecture

This document describes main on Oct 31, 2026, including level 3 wiring
and the current validation tooling. Levels 1 and 2 closed on Oct 27.

## Startup and serving

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

[app.py](backend/src/oversight/app.py) composes the Python 3.12 FastAPI backend.
[settings.py](backend/src/oversight/settings.py) reads configuration from the environment
and `.env`. [setup.py](backend/src/oversight/setup.py) checks the offered slots' setup
without calling a provider. Health reports missing settings without exposing their values.

[Makefile](Makefile) provides the entry points:

- `make dev` starts FastAPI with reload and Vite. Both processes receive resolved `OVERSIGHT_PORT`
  (8710) and `OVERSIGHT_VITE_PORT` (launcher fallback 5710), with environment precedence over `.env`.
  Vite uses strictPort and proxies API routes while preserving Host and Origin.
  The SSE proxy streams without compression or buffering.
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

The backend runs `git init` in the copied workspace. `.runtime/` is gitignored.
The demo template is plain files with no git of its own: a Python project written here,
with 4–6 modules, 20–40 functions and tests, and one central function with many callers.
After the hackathon, `desktop/` will hold a Tauri shell with the backend inside WSL,
before the native Windows and macOS backend ports. The demo runs in the browser.
See [Opening the person's own repository](docs/own-repository.md) for its separate plan and rules.

## Data flow

1. `LiveRun` publishes `run.started` with model profiles, slots, prompt version, limits and
   budget. It scans main and publishes `graph.snapshot` with the saved demo positions.
2. `POST /agents` checks the task, model, setup, spend brake and slot availability.
   Up to six slots (A–F), four by default, can run in parallel, each with its own
   worktree and model client.
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
5. Every completed tool call emits `agent.step`. Read focus precedes the step; `list_files`
   sends none, and `search` focuses only its first five matching nodes. Successful edits publish
   `change.updated`, edit focus and then the step. Updates carry full original and modified file
   contents, author and a pending graph overlay. Model replies produce `usage.tick`; the loop
   does not stream model text.
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

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

The backend uses Python 3.12, uv and FastAPI. The graph core parses Python `ast` into nodes
and edges. Changed files are reparsed after every edit, merge or discard, driven by each
file's content hash.
For the person's own repository, a watcher only triggers that same rescan path.
The runtime's small client interface allows the provider format to be swapped. Model and
endpoint are set per agent: agent B may use Muse Spark Open
(`meta-models/muse-spark-open-1026`) through Baseten's Chat Completions API.
The event store uses stdlib `sqlite3`.

Cross-package imports use each package's `__init__.py`. The import-linter rules in
[pyproject.toml](pyproject.toml) enforce `app -> api -> changes -> agents -> graph | events |
workspace`. The bottom three packages are independent. Protected contracts keep package
internals private. Contracts import neither the backend nor web/model frameworks. Only agents
may import OpenAI; only app and API may import FastAPI or Starlette.

- [API](backend/src/oversight/api/__init__.py) exports `Commands`, route construction, error
  mapping and static serving. `Commands` covers, for example, start, stop, approve, reject,
  request_changes, get_file, save_file and rescan; its full interface is in `api/commands.py`.
  The package owns HTTP responses and SSE framing.
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

### Graph rules

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

- A call is "exact" only when its name resolves to a single function; everything else is
  "possible", with its reason.
- Methods are nodes from day one. A function's range starts at its first decorator.
- Comprehensions, generator expressions, lambdas and nested functions are not nodes. Their
  calls belong to the nearest enclosing function or method, because it runs them or returns
  them. Names resolve in the nested scope first: a parameter, a comprehension target or a
  local assignment hides a module-level function of the same name. Exactness follows
  resolution only: a call that may not run, such as one inside an `if`, is still "exact"
  if its name binds to one function.
- Calls at module level belong to the module node; calls in a class body belong to the class node.
- Imports resolve: relative imports differ for a package `__init__.py` and a plain module,
  and `import a.b` binds the name `a`. Star imports, rebound names, attribute calls on unknown
  receivers, and methods a framework calls by name are "possible".
- Node ids never include line numbers. Two definitions with the same qualified name, such as
  a property getter and setter, get a suffix.

### Impact cone

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

The cone is a reverse BFS over call edges. The data flow above specifies its depth and
node-count bounds, omissions and agent-version proposals; DESIGN.md's Impact cone section
specifies the UI's "+12 omitted by depth" and "possibly affected", never a complete list.

### Agent tools

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

The data flow above specifies the six tools, per-call steps, read and edit focus, the first
five search matches, unique-match and last-read-hash edit checks, and the fixed test command.
A worktree does not isolate: it shares `.git/config` and the stash, hence the no-shell rule.
Read focus uses read mode; edit focus uses edit mode.

- `read_file` returns numbered lines and has a size cap; long files are read by line range.
  Prefer few, large reads of whole functions: every step resends the full history.
- If `edit_file` fails because the old text does not appear exactly once or the file changed
  since the agent last read it, the error goes back to the model.
- No tool returns the content of an obvious secret file, even a tracked one:
  `.env*`, `*.pem`, `*.key`, `id_*`. The maintainer's 2026-10-31 secret-filter decision in
  DECISIONS.md narrows `id_*` to SSH identity names.
- The test environment is never the backend's virtual environment.
- Tool results carry no extra metadata.

The Agents module contract above specifies the four-identical-results and six-failures stop
limits; the data flow specifies that status comes from tool execution, never model text.

### Pending changes, approval and conflicts

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

Each agent records its base commit. The data flow above specifies combined-result tests
when main moved, Request changes keeping the worktree and comment, and review edits with person attribution
that invalidate the agent's earlier read.
If a merge conflicts or tests fail, mark the change. The person chooses between asking the
agent to redo it on the new base and rejecting it. DESIGN.md's Review panel and Live diff
and file view sections specify review-only editing while the agent is idle.

### Test coverage badges

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

The level 3 "no tests" badge comes from coverage: `run_tests` runs with pytest-cov (MIT),
and covered lines map to functions by their line ranges. If that is not ready, the badge
reads "no test calls it" and is computed from the call graph.

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

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

The frontend uses React, strict TypeScript, Vite and pnpm. Monaco's workers are configured
in Vite. Agent and review panels use Tailwind CSS; Cytoscape and Monaco keep their own
styling. Icons come from lucide-react.

[eslint.config.js](frontend/eslint.config.js) requires cross-feature imports through `index.ts`,
including dynamic imports. Only transport may use `fetch` or `EventSource`. Only graph imports
Cytoscape/fcose; only editor imports Monaco. State cannot import React or React DOM.

- [State](frontend/src/state/index.ts) exports the pure reducer, Zustand store, selectors and
  spend helpers. Main's graph and pending change graphs stay separate.
- [Transport](frontend/src/transport/index.ts) owns live SSE, replay and command requests.
  It checks event envelopes; generated TypeScript types do not validate payloads at runtime.
- [App](frontend/src/app/index.ts) composes panels, owns transport lifetime and local view
  selection, and keeps file drafts and saves across panel changes.
- [Agents](frontend/src/agents/index.ts) presents the offered slots A–F (four by default), status, steps,
  per-agent spend, task entry, Pause/Resume and Stop and discard.
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

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

Pydantic models in [contracts/src/oversight_contracts](contracts/src/oversight_contracts) are the
source. [generate.py](contracts/generate.py) generates JSON Schema and TypeScript with pinned
TypeScript and json-schema-to-typescript. `make check-contracts` compares both generated
outputs byte for byte, including in CI.

The 25 event types include, for example, `run.started`, `graph.snapshot`, `graph.delta`,
`agent.status`, `agent.step`, `agent.message`, `agent.focus`, `change.updated`, `change.proposed`,
`change.resolved`, `lead.reply`, `usage.tick` and `error`.
The complete `ServerEvent` union is in `contracts/src/oversight_contracts/events.py`.
Every envelope has `seq`, `ts`, `type` and `payload`.
`run.started` includes the run id, exact model, prompt version and limits.
`agent.status` includes `revising` after Request changes and `paused` in level 3.
`agent.step` is emitted once per tool call when it ends, carrying the step number, tool,
short summary, result (`ok` or `error`) and duration; it feeds the agent card.
The full contents in `change.updated` are required by Monaco's DiffEditor; replay must never
depend on worktrees that no longer exist. In addition to the proposal fields in the data flow,
`change.proposed` carries the summary and each cone entry's node and distance.
`change.resolved` carries the decision (`approved`, `changes_requested` or `rejected`), the person's comment and the
merge result (`merged` or `conflict`). Never one event per token: model text is batched
every few hundred milliseconds and SQLite writes are batched.
Events arrive only through SSE at `/events`; the data flow above specifies starting at seq 1,
gap recovery and `GET /graph` reading the snapshot and its seq together.
`agent.message` records accepted human text to a coding agent or lead, masked before storage.
Contracts 1.15.0 declares `lead.reply {agent_id, answers_seq, text, through_seq, cut}` for L2.
`answers_seq` is null for the opening task, or names an earlier `agent.message` to the same lead.
`through_seq` is null or a nonnegative horizon strictly below the reply's seq, with no target-type
requirement. Non-null reply text is nonblank and at most 4,096 characters, masked before storage
like messages. `cut` is `tool_calls`, `model_calls`, `time`, `spend_brake` or null. Null text requires
a cut in ordinary/live validation; publication validation also accepts withheld text with null
cut under the existing message-text receipt. Both event types withhold text by default on export;
explicit inclusion authorization includes the stored masked text. No new wire receipt fields exist.
`LeadStatus.state` gains `idle` between turns; `done` remains accepted for historical logs.
L1 accepts replies as no-ops: only envelope sequence/clock bookkeeping advances, with no messages,
decisions, activity or accounting observations and no thread UI. Idle adds no control behavior.

Live routes implement, for example, `POST /agents`, `POST /agents/{agent_id}/stop`,
`POST /agents/{agent_id}/message`, `POST /agents/{agent_id}/pause`,
`POST /agents/{agent_id}/resume`,
`POST /changes/{change_id}/approve`, `POST /changes/{change_id}/reject`,
`POST /changes/{change_id}/request-changes`, `POST /grants/{grant_id}/revoke`, `GET /files`,
`PUT /files` and `POST /graph/rescan`.
`GET /graph`, `GET /health` and `GET /events` provide graph, readiness and events.
The complete route registrations are in `api/commands.py` and `api/routes.py` under
`backend/src/oversight/`; request and response models are in `contracts/`.

| Read route (live and replay) | Response |
| --- | --- |
| `GET /agents/{agent_id}` | 200 latest committed coding `AgentStatus`; 404 `not_found` otherwise. |
| `GET /changes/{change_id}` | 200 archived `ChangeView` at one committed seq, with usage and confirmed revision rounds (null when unavailable); 404 `not_found` otherwise. |

L1 also declares `POST /leads/{agent_id}/message` with the existing `AgentMessage` body:
empty 202 on acceptance, 409 `wrong_state` unless idle, and 501 in replay. It has no installed
handler in L1. L2 implements admission and publishes the existing `agent.message` with the lead
id as recipient, plus `lead.reply` production; L3 presents the Chat thread. The approved thread
belongs to one run and is lost on server restart; restart already stops the lead. Persistence
is a later design step.

The installed coding-agent Message, Pause and Resume routes accept only human HTTP commands
and answer an empty 202.
The manager's public eligibility check admits only working changes; the drive also refuses
controls once proposal or halt finalization begins. Preparation/decisions, review, conflicts,
broken and closed changes return 409 `wrong_state`; unknown agents return 404 `not_found`.
All three routes answer 501 in replay before reading the body. No lead or agent tool exposes
these controls. Pause and Resume are mounted on the agent card. The Conversation
panel and its message composer are mounted as a focus-panel tab by
`frontend/src/app/shell/FocusPanel.tsx`, opened from the agent card.

Messages queue in arrival order until the next safe model-call boundary. Accepted text persists
in `agent.message`, masked before storage; the agent receives the original. Publication exports
withhold message text by default, with explicit inclusion available. Admission
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

`GET /impact`, merge-mode, plan, queue, history and existing lead start/read/stop routes are implemented.
`GET /tree?agent=` traverses the selected workspace and returns its file inventory.
Only the `NOT_BUILT` routes in `api/commands.py` remain unconditional 501 responses:
`POST /dictation` and `POST /changes/{change_id}/review`.
The Revert route answers 202 after synchronous admission, loads its target from this run's
committed log, and reports its outcome only through events.
`change.reviewed` (when enabled), `change.facts_recorded`, `tests.executed`,
`queue.updated` and `lead.status` have live producers, as do `change.assessed`,
`merge_mode.changed` and `plan.updated`. Rules activation is open; publication alone does not authorize a rules merge.
Approve, Reject and Request changes require the revision and head shown by the review render;
revision zero is limited to rejecting the published agent base before any proposal.
During a rules decision, the person’s Reject or Request changes is admitted until rules
authorization starts and cancels the rules attempt; from that cutoff it answers 409.
Each confirmed main commit parks idle armed rules waits; once the graph snapshot holds that
commit, a new generation renews them against that main with the original absolute deadline.
A queued rules attempt finding an announced newer main publishes no notice and, once its decision
ends, a new generation re-assesses against that main with the original deadline, a fresh combined
suite and fresh spend; every in-flight outcome closes following.
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

`make check` runs five groups in parallel: Python, contracts, frontend, secrets and
third-party notices (`check-notices`). It runs Ruff,
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
real recording from `fixtures/events/recorded-demo.jsonl` and its provenance sidecar; the public
build requires both. UI tests explicitly select their fixtures. `make record` exports a finished
real run with a provenance sidecar. The build checks provenance, contracts and secrets.
`make e2e` runs 15 Playwright tests in eight spec files, including level 1 and scripted level 2
replays, practice, Files, editor startup/prefetch, subpath serving and third-party notices.

## Level 3 wiring and remaining private cores

Contracts are at 1.11.0 (`contracts/src/oversight_contracts/__init__.py`). Policy assessments,
run-rule findings, before/after test evidence and optional cross-review are wired into the live
flow. Behavior diff and measured caller evidence are built and opt-in, off by default.
Merge-mode controls, standing grants, follow-main for idle rules waits and Revert are wired,
and `changes/_mode.py` opens rules activation. Every fresh run starts in Ask me; explicit
selection still requires C, trustworthy main and independent cross-review, and every automatic
merge requires all nine rules and transaction guards. Clean rules merges offer person-only Revert. Plans, dependency dispatch, the queue and the lead runtime are
wired; the lead requests plans and leaves their approval to the person.
`merge_mode.changed` confirms selection using the existing CAS revision and sets the mode shown
in Settings and the status line. Committed runtime review receipts are forwarded to the existing
rules receipt port, just as manager publications are. `make rules-check` shares demo-check's
shutdown, descendant and runtime-lock checks for three consecutive rules scenarios. The separate
`scripted-rules.jsonl` and provenance are fake evidence, validated with `--fake-fixture`; public
replay still bundles the real recording and stays read-only.

`changes/_reviewfacts` remains a private unwired core. The `_zones` and `_partition` algorithms
also remain outside live plan production; the public partition export does not wire a producer.
Future contract versions take the next free version at landing.
