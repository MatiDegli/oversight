# Proposal: structure, contracts v1, and build order

Status: approved by the maintainer on 2026-10-26, with his answers to section 8 folded in. The
controls in section 7 were added the same day at his request, and merge approval moved to the
coordinator, with the critical decisions kept by the maintainer (section 7). Times are in Argentina time (ART,
UTC-3). [AGENTS.md](../AGENTS.md) stays the brief; this document adds what
it does not settle and does not repeat it.

## 1. Summary

- Five code areas: `contracts/` (Pydantic, the source of truth), `backend/` (one Python package with
  six modules behind facades), `frontend/` (React with feature folders), `demo-repo/` (the template),
  and `fixtures/` (event logs, fake-model scripts and the preset layout, shared by every test and by
  replay).
- import-linter checks five contracts on the backend: layers, private internals, pure contracts,
  one gate to the model API, and the web framework kept to the HTTP layer. ESLint's built-in rules
  check the same kind of boundary in the frontend.
- Contracts v1: the eleven events of AGENTS.md, unchanged in name, with exact payloads; its
  thirteen commands, three of them answering 501 until level 3, plus the event stream and a new
  `GET /health`; one error format; stable ids for runs, agents, changes, nodes and edges.
- One protocol for live and replay: the UI applies the run's event log from `seq` 1 with one pure
  reducer, and resumes from the last `seq` it applied.
- Eighteen fronts to level 1, with one acceptance command each. The critical path to Thursday goes
  through contracts, the event bus, the agent runtime and the change manager.
- Controls sized for nine days: hooks for commit messages, commit size and pre-push, hardened CI,
  a 14-day release age and `make audit`. Section 7 lists them, and what was left out and why.
- Section 8 lists where this proposal settles or departs from AGENTS.md and the plan, with the
  maintainer's answer to each row.

## 2. Directory tree

```text
oversight/
├── AGENTS.md  CLAUDE.md  README.md  ARCHITECTURE.md  DECISIONS.md  DEVLOG.md  LICENSE
├── Makefile                  # the only entry point: setup, dev, check, contracts, demo, replay, ...
├── pyproject.toml            # uv workspace (members: contracts, backend) and the tool settings:
├── uv.lock                   #   ruff, pyright, pytest, import-linter
├── .python-version           # 3.12
├── .env.example              # variable names only, never values
├── .github/workflows/ci.yml
├── docs/
│   └── proposal.md
├── contracts/
│   ├── pyproject.toml        # package oversight-contracts; depends on pydantic only
│   ├── src/oversight_contracts/
│   │   ├── __init__.py       # CONTRACT_VERSION and the public names
│   │   ├── common.py         # ids, paths, timestamps, shared literals
│   │   ├── graph.py          # GraphNode, GraphEdge, GraphSnapshot, GraphDelta, ChangeGraph, ImpactCone
│   │   ├── events.py         # the envelope, the eleven payloads, the ServerEvent union
│   │   └── commands.py       # request and response bodies, ErrorResponse
│   ├── generate.py           # writes generated/: JSON Schema, then TypeScript through json2ts
│   ├── generated/            # written only by `make contracts`; never edited by hand
│   │   ├── oversight.schema.json
│   │   └── contracts.ts
│   └── tests/
├── backend/
│   ├── pyproject.toml        # package oversight: fastapi, uvicorn, openai, oversight-contracts
│   ├── src/oversight/
│   │   ├── __main__.py       # CLI: serve, replay a log, export a run
│   │   ├── app.py            # composition root: builds the app and wires the modules
│   │   ├── settings.py       # configuration from the environment and .env; only app.py reads it
│   │   ├── api/              # HTTP: routes, SSE framing, error format, static files
│   │   ├── events/           # event store, single writer, subscribers, replay player, JSONL import/export
│   │   ├── graph/            # extract, resolve, diff, span index, change graph, impact cone
│   │   ├── workspace/        # runtime workspace, async process and git runner, locks, path checks, test runner
│   │   ├── agents/           # model clients, fake model, tools, tool loop, limits, usage ledger
│   │   └── changes/          # change records, proposals, approve, request changes, reject, user edits
│   └── tests/                # one folder per module, plus e2e/: the whole backend through the fake model
├── frontend/
│   ├── package.json  pnpm-lock.yaml  tsconfig.json  vite.config.ts  eslint.config.js  index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── app/              # shell: top bar, watch and review layouts, the side panel's modes
│   │   ├── state/            # applyEvent (pure), the Zustand store, selectors
│   │   ├── transport/        # live SSE with resume, the replay player, the command client
│   │   ├── graph/            # the Cytoscape component, stylesheet, positions, focus and cone classes
│   │   ├── editor/           # Monaco from the local package with its workers, diff panel, file view
│   │   ├── agents/           # agent cards, steps, the new-task form
│   │   ├── review/           # review header, files, cone list, decision bar
│   │   └── theme/            # tokens, fonts, Tailwind theme
│   └── e2e/                  # Playwright over replay
├── fixtures/
│   ├── events/               # event logs, .jsonl: hand-written ones and recorded runs
│   ├── fake-model/           # scripted tool calls for the fake model, one file per task
│   └── layouts/              # preset positions by workspace: demo-repo.json
├── demo-repo/                # the template: a small shop project with tests, no git of its own
├── tools/                    # development scripts: the API probe, the contract-scope check
└── desktop/                  # level 3 only; created then
```

Notes:

- The uv workspace gives one lock and one virtual environment for contracts and backend. The demo
  repo is not a member: `run_tests` uses its own environment, `.runtime/demo-venv/`, created on
  start.
- Generated TypeScript lives with the contract, in `contracts/generated/`. The frontend imports it
  through the alias `@contracts`.
- `fixtures/` sits at the top because the backend tests, vitest, Playwright and the static site all
  read the same files.
- At run time: `.runtime/workspace/` (main), `.runtime/worktrees/{agent_id}/`,
  `.runtime/runs/{run_id}.sqlite` (one event store per run), `.runtime/ledger.sqlite` (spend across
  runs, kept on a clean start), `.runtime/demo-venv/`. All gitignored.

## 3. Module boundaries

### Backend modules

| Module | Owns | May import |
| --- | --- | --- |
| `app`, `__main__`, `settings` | wiring, configuration, the CLI | every facade |
| `api` | routes, SSE framing, error format, static files | `changes`, `agents`, `graph`, `events` |
| `changes` | each change from first edit to merge or discard; user edits | `agents`, `graph`, `events`, `workspace` |
| `agents` | model clients, tools, the tool loop, limits, usage | `graph`, `events`, `workspace` |
| `graph` | parsing, resolution, deltas, spans, change graphs, the impact cone | contracts only |
| `events` | the store, the single writer, subscribers, replay, export | contracts only |
| `workspace` | the runtime workspace, worktrees, the process and git runner, locks, paths, tests | contracts only |

`agents` never imports `changes`. The agent runtime reports edits and `finish` through a small
protocol it defines (`ChangeSink`); `changes` implements it, and `app` wires the two. Each module
exposes its facade in `__init__.py`; everything else in it is internal.

### import-linter contracts

All five run in `make check` (`lint-imports`), with `root_packages = ["oversight",
"oversight_contracts"]` and external packages included.

1. **Layers** (`layers`): `oversight.app` > `oversight.api` > `oversight.changes` >
   `oversight.agents` > `oversight.graph | oversight.events | oversight.workspace`. The three in the
   bottom layer are independent of each other.
2. **Private internals** (`protected`): for each module, its submodules are imported only from
   inside that module. Others go through the facade.
3. **Pure contracts** (`forbidden`): `oversight_contracts` imports neither `oversight` nor fastapi,
   starlette, or openai.
4. **One gate to the model API** (`forbidden`): only `oversight.agents` imports `openai`.
5. **Web framework at the edge** (`forbidden`): `graph`, `events`, `workspace`, `agents` and
   `changes` import neither fastapi nor starlette.

Checked by ruff, since import-linter does not reach standard-library functions:

- `subprocess` and `os.system` are banned outside the workspace runner (`flake8-tidy-imports`
  banned-api, with one per-file exception). Every subprocess goes through one async runner with a
  timeout that kills the whole process group.
- The `ASYNC` rules catch blocking calls inside async functions.

### Frontend boundaries

ESLint's built-in `no-restricted-imports` and `no-restricted-globals`, per folder:

- `state/` imports no React, Cytoscape or Monaco: the reducer stays pure.
- Only `graph/` imports cytoscape; only `editor/` imports monaco-editor.
- Only `transport/` uses `fetch` and `EventSource`.
- Feature folders import each other only through their `index.ts`.

## 4. Contracts v1

### 4.1 Wire conventions

- JSON with snake_case names on both sides; the TypeScript types keep the same names.
- Every model forbids extra fields, so the JSON Schema says `additionalProperties: false` and the
  TypeScript types have no index signatures.
- In events and responses every key is always present: a missing value is `null`. Request bodies
  may omit optional keys.
- Paths are relative to the workspace root, with forward slashes and no `.` or `..` segments; never
  absolute, and never inside `.git/`. A worktree uses the same paths as main.
- No event carries an absolute path, since recorded logs will be public. The server strips the
  workspace's own location from every text it puts in an event, such as error messages and test
  output.
- Lines are 1-based, and ranges include both ends.
- `ts` is UTC with millisecond precision and a `Z`: `2026-10-27T14:03:12.345Z`.
- Durations are integer milliseconds (`*_ms`); money is estimated US dollars (`*_usd`).
- Token counts are `int | null`, where `null` means the API did not report it. Never 0 for unknown.
- Type names avoid the browser's globals: `GraphNode`, not `Node`; `ServerEvent`, not `Event`;
  `FocusRange`, not `Range`.

### 4.2 Identifiers

| Id | Format | Example |
| --- | --- | --- |
| Run | `run-` and the UTC start time | `run-20261027-140312` |
| Agent | `agent-` and a counter per run | `agent-1` |
| Change | `change-` and a counter per run; one change per agent | `change-1` |
| Slot | `A` or `B`; the UI maps it to the color | `A` |
| Module node | dotted module path from the repository root; a package's `__init__.py` is the package | `shop.pricing` |
| Other node | module, `:`, qualified name | `shop.models:Product.total` |
| Duplicate node | `#n` from the second definition of a name on, in source order | `shop.models:Product.price#2` |
| Edge | kind, `:`, source, `->`, target | `calls:shop.orders:create_order->shop.pricing:compute_price` |

Node ids never hold line numbers. Run, agent and change ids come from one injectable id source, so a
fake-model run writes the same ids every time.

### 4.3 Graph models

All models are frozen and forbid extra fields.

```python
NodeKind = Literal["module", "class", "function", "method"]
EdgeKind = Literal["imports", "calls", "inherits"]
Confidence = Literal["exact", "possible"]
PossibleReason = Literal[
    "unknown_receiver",    # attribute call whose receiver type is unknown
    "several_candidates",  # the name binds to more than one definition
    "rebound_name",        # the name is reassigned, so it may not hold the function
    "star_import",         # the name may come from a star import
    "dynamic_dispatch",    # called by name at run time: getattr, a framework hook
]

class GraphNode:
    id: NodeId
    kind: NodeKind
    name: str                  # "total"; a module's last segment
    qualname: str              # "Product.total"; a module's dotted path
    path: str                  # "shop/models.py"
    start_line: int            # the first decorator
    end_line: int
    parent_id: NodeId | None   # module for top-level definitions, class for methods; None for modules
    is_test: bool              # in a test module; hidden by default, never listed in a cone

class GraphEdge:
    id: EdgeId
    kind: EdgeKind
    src: NodeId
    dst: NodeId
    confidence: Confidence
    reason: PossibleReason | None   # set exactly when confidence is "possible"

class Point:
    x: float
    y: float

class ParseError:
    path: str
    line: int | None
    message: str

class GraphSnapshot:
    version: str                     # hash of every file's hash
    commit: str                      # main's HEAD the graph was parsed from
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    parse_errors: list[ParseError]   # files kept at their last good parse
    positions: dict[NodeId, Point]   # the saved preset layout; {} when there is none

class GraphDelta:
    from_version: str
    to_version: str
    commit: str
    cause: Literal["merge", "user_edit", "rescan"]
    nodes_added: list[GraphNode]
    nodes_changed: list[GraphNode]   # same id, other fields
    nodes_removed: list[NodeId]
    edges_added: list[GraphEdge]
    edges_changed: list[GraphEdge]   # same id, other confidence or reason
    edges_removed: list[EdgeId]
    parse_errors: list[ParseError]   # the full current list

class ChangeGraph:                    # one change against its own base commit
    nodes_added: list[GraphNode]     # in the change's version, not in its base
    nodes_modified: list[NodeId]     # own source differs from the base
    nodes_removed: list[NodeId]      # in the base, gone from the change's version
    edges_added: list[GraphEdge]     # to draw the added nodes' calls
    edges_removed: list[EdgeId]

class ImpactEntry:
    node_id: NodeId
    distance: int                    # 1 calls a seed directly

class ImpactCone:
    seeds: list[NodeId]              # the change's added and modified nodes
    entries: list[ImpactEntry]       # by distance, then id; seeds and tests left out
    edges_followed: Literal["exact", "exact_and_possible"]   # "exact" in v1
    max_depth: int
    max_nodes: int
    omitted_by_depth: int            # reachable beyond max_depth
    omitted_by_limit: int            # cut by max_nodes
    tests_reaching: int              # test functions whose exact calls reach a seed, at any depth
```

The graph rules are AGENTS.md's, including its rules on nested scopes and imports, which differ from
the plan's. Four points it leaves open, settled here:

- **Containment is `parent_id`.** There are no `contains` edges: Cytoscape draws containment as
  nesting, and two encodings of one fact can disagree.
- **A node's own source** excludes the bodies of the nodes nested in it. A changed method marks the
  method modified, not its class; a new module-level constant marks the module.
- **Calling a class** is a call to the class node. A change to its `__init__`, `__new__` or
  `__post_init__` also seeds the class, so the constructors' callers enter the cone.
- **Pending changes stay out of main's graph.** Each change carries its `ChangeGraph` against its own
  base, which only its own edits change. When main moves, the UI draws the same overlay on the new
  main.

### 4.4 Events

Every event is an envelope:

```python
class EventBase:
    seq: int    # from 1, +1 per event within a run, no gaps; assigned by the single writer
    ts: str     # UTC, as in 4.1
    type: str   # one of the eleven below; each event class narrows it to a literal
    payload: ...

ServerEvent = Annotated[RunStartedEvent | GraphSnapshotEvent | ... | ErrorReportEvent,
                        Field(discriminator="type")]
```

**`run.started`**: the first event of every run.

```python
class ModelProfile:
    id: str                    # "muse-spark", "muse-spark-open", "fake"
    label: str                 # "Muse Spark"
    provider: Literal["meta", "baseten", "fake"]
    model: str                 # the exact id sent to the API, e.g. "meta-models/muse-spark-open-1026"
    reasoning: str | None      # the reasoning setting sent on every call; None if the API has none

class SlotInfo:
    slot: Literal["A", "B"]
    default_model: str         # a ModelProfile id

class Limits:
    max_steps: int             # tool calls per task
    max_output_tokens: int     # per model call
    max_wall_clock_s: int      # per task
    test_timeout_s: int
    cone_max_depth: int
    cone_max_nodes: int

class Budget:
    budget_usd: float
    spent_usd: float           # from the ledger, before this run
    warn_at: list[float]       # [0.5, 0.8]
    block_at: float            # 0.9: above it the server refuses new agents

class RunStarted:
    run_id: str
    contract_version: str
    prompt_version: str        # a name and a hash of the system prompt and tool schemas
    workspace: str             # "demo-repo"
    base_commit: str           # main's first commit in the runtime workspace
    models: list[ModelProfile]
    slots: list[SlotInfo]
    limits: Limits
    budget: Budget
```

**`graph.snapshot`**: payload `GraphSnapshot`. Sent once per run, right after `run.started`.

**`graph.delta`**: payload `GraphDelta`. Main's graph only, after a merge, a user edit on main, or
a rescan that changed something.

**`agent.status`**: the agent's full status, sent on every change of state.

```python
AgentState = Literal["starting", "working", "ready_for_review", "revising",
                     "paused", "halted", "done", "stopped"]   # "paused" is level 3

class AgentStatus:
    agent_id: AgentId
    slot: Literal["A", "B"]
    change_id: ChangeId
    task: str
    model: str                 # a ModelProfile id
    state: AgentState
    step: int                  # tool calls finished so far
    max_steps: int             # grows when a revision starts
    detail: str | None         # why it halted or stopped, in one sentence
```

**`agent.step`**: one per tool call, when the call ends.

```python
class AgentStep:
    agent_id: AgentId
    step: int                    # 1-based
    tool: str                    # the tool the model named, even one that does not exist
    summary: str                 # built by the server from the tool and its arguments, never from model text:
                                 # "Read restock in inventory.py, lines 8 to 12", "Ran tests: 23 of 24 pass"
    result: Literal["ok", "error"]
    error: str | None            # the error returned to the model, shortened
    duration_ms: int
    tests: SuiteResult | None    # set for run_tests

class SuiteResult:               # not "TestResult": pytest would try to collect it
    outcome: Literal["passed", "failed", "error", "timeout"]
    passed: int
    failed: int                  # failures and errors
    skipped: int
    duration_ms: int
    failed_tests: list[str]      # pytest node ids, 20 at most
```

**`agent.focus`**: what an agent reads or edits.

```python
class FocusRange:
    path: str
    start_line: int | None       # None: the whole file
    end_line: int | None

class AgentFocus:
    agent_id: AgentId
    mode: Literal["read", "edit"]
    ranges: list[FocusRange]     # search may cover several files
    node_ids: list[NodeId]       # the innermost nodes over the ranges, resolved on the agent's own copy
                                 # a search covers only its first 5 matching nodes, in both lists
```

**`change.updated`**: after every edit, by the agent or by the user.

```python
class FileDiff:
    path: str
    status: Literal["added", "modified", "deleted"]
    original: str | None         # the whole file at the change's base; None if added
    modified: str | None         # the whole file in the change's version; None if deleted
    lines_added: int
    lines_removed: int

class ChangeUpdated:
    change_id: ChangeId
    agent_id: AgentId
    author: Literal["agent", "user"]
    file: FileDiff               # the file this edit touched
    node_ids: list[NodeId]       # nodes of that file the change adds, modifies or removes
    graph: ChangeGraph           # the whole change against its base, after this edit
    files_changed: int           # totals for the whole change
    lines_added: int
    lines_removed: int
```

**`change.proposed`**: when the agent calls `finish`, when its run halts on its own, and again after
each revision.

```python
class FileStat:
    path: str
    status: Literal["added", "modified", "deleted"]
    lines_added: int
    lines_removed: int

class ChangeProposed:
    change_id: ChangeId
    agent_id: AgentId
    revision: int                # 1, then +1 after each Request changes
    task: str
    summary: str | None          # the agent's note, from finish; None when the run halted
    base_commit: str
    head_commit: str             # the commit on the agent's branch that holds this revision
    files: list[FileStat]
    lines_added: int
    lines_removed: int
    graph: ChangeGraph
    tests: SuiteResult           # run by the server on head_commit, through run_tests' runner and timeout
    impact: ImpactCone           # computed on the change's version
```

**`change.resolved`**: a decision in review, or a stop. Every change ends with one.

```python
class Conflict:
    kind: Literal["merge", "tests"]
    paths: list[str]             # conflicting files, for a merge conflict
    tests: SuiteResult | None    # the combined result's tests, for a test failure

class ChangeResolved:
    change_id: ChangeId
    agent_id: AgentId
    revision: int                # 0 when the change was never proposed
    decision: Literal["approved", "changes_requested", "rejected"]
    reason: Literal["stopped"] | None   # "stopped": the agent was stopped and its change discarded
    comment: str | None
    merge: Literal["merged", "conflict"] | None   # set only when approved
    merge_commit: str | None                       # main's new HEAD, when merged
    conflict: Conflict | None                      # set when merge is "conflict"
```

**`usage.tick`**: one per model call.

```python
class UsageTick:
    agent_id: AgentId
    model: str | None            # the model id the API reported for this call
    input_tokens: int | None     # None: not reported
    output_tokens: int | None
    cached_tokens: int | None
    cost_usd: float | None       # None: unknown
    spent_usd: float             # the ledger's total, this call included
    budget_usd: float
    brake: Literal["ok", "warn_50", "warn_80", "blocked"]
```

**`error`**: payload `ErrorReport`. Failures shown without breaking the demo.

```python
class ErrorReport:
    source: Literal["model_api", "agent", "tools", "git", "tests", "graph", "server"]
    message: str
    agent_id: AgentId | None
    change_id: ChangeId | None
    fatal: bool                  # the agent's run halted because of it
```

v1 sends no model text, so nothing streams per token. If an event ever carries model text, it is
batched, as AGENTS.md says.

### 4.5 Order and lifecycles

- **Order within a tool call.** An edit sends `change.updated`, then `agent.focus`, then
  `agent.step`, so the UI knows a new node before a focus names it. A read sends `agent.focus`, then
  `agent.step`. `list_files` reads no source and sends no focus. A search focuses only its first 5
  matching nodes, so a broad search does not light up the whole graph.
- **Agent.** `starting` → `working` → `ready_for_review` → `done` after a merge or a rejection.
  Request changes: `ready_for_review` → `revising` → `ready_for_review`. Stop, from any state
  before `done`: `stopped`.
- **A run that ends on its own**, at the step limit, the no-progress cutoff or an error, goes to
  `halted`, with the reason in `detail`. Its change is proposed as it stands and stays open for
  review; Request changes on it starts a revision with a new step budget.
- **Change.** It opens with the agent. `change.proposed` makes it ready for review. Approve merges
  it, or marks it `conflict` and keeps it open for Request changes or Reject. Request changes sends
  it back to the agent with the comment; Reject discards it. Every change ends with
  `change.resolved`, so the reducer and the history have one closing path.
- **Stop.** Stopping an agent discards its change: the server sends `agent.status` with `stopped`,
  then `change.resolved` with `rejected` and the reason `stopped`.
- **Merge.** An approval that merges sends `change.resolved`, then `graph.delta` with cause
  `merge`, then `agent.status` with `done`.

### 4.6 The event stream

- `GET /events` answers `text/event-stream`. Each message is `id: <seq>` and `data: <the envelope as
  JSON>`, with no `event:` field, so one `onmessage` handler receives every type. A `: ping` comment
  goes every 15 seconds. Headers: `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering:
  no`, never compressed.
- The resume point is the `Last-Event-ID` header, else the `after` query parameter, else 0. The
  server sends the stored events after it, then live ones, in `seq` order.
- `?run=<run_id>`: if it names another run, the server ignores the resume point and starts at
  `seq` 1. A `run.started` for a new run resets the UI's state, so a tab left open across a restart
  recovers by itself.
- **Runs.** Each start of the server, such as each `make demo`, is a new run with its own log.
- **Connect.** The UI applies the run's events from `seq` 1 with the same reducer replay uses. The
  log of one run is small, and agent and change state exist only in events.
- **Gap.** If `seq` is not the last one plus 1, the UI reconnects with `after=<last applied>`. The
  store is complete, so nothing is lost.
- `GET /graph` returns the graph and the `seq` it reflects, read together. The smoke test, debugging,
  and any client that needs only the graph use it.
- SSE uses FastAPI's own `fastapi.sse` (present in 0.162.0), with no extra dependency.

### 4.7 Commands

A 202 means only that a command was accepted; its results arrive as events. A command that cannot
be accepted, such as approving a change that is not ready for review, fails at once with a 4xx in
the single error format.

| Command | Body | Success | Errors |
| --- | --- | --- | --- |
| `POST /agents` | `StartAgent {task, slot?, model?, zone?}` | 201 `AgentCreated {agent_id, change_id, slot}` | `slot_busy`, `not_configured`, `spend_brake` |
| `POST /agents/{id}/stop` | none | 202 | `not_found`, `wrong_state` |
| `POST /agents/{id}/message` | `{text}` | 202 | `not_found`, `wrong_state`, `invalid_request` |
| `POST /agents/{id}/pause` | none | 202 | `not_found`, `wrong_state` |
| `POST /agents/{id}/resume` | none | 202 | `not_found`, `wrong_state`, `not_configured`, `spend_brake` |
| `POST /changes/{id}/approve` | `{revision, head_commit, comment?}` | 202 | `not_found`, `wrong_state` |
| `POST /changes/{id}/request-changes` | `{revision, head_commit, comment}`, not empty | 202 | `not_found`, `wrong_state` |
| `POST /changes/{id}/reject` | `{revision, head_commit, comment?}` | 202 | `not_found`, `wrong_state` |
| `GET /agents/{id}` | none | Reserved: 501; then 200 `AgentStatus` for coding agents | `not_implemented` |
| `POST /changes/{id}/review` | `ReviewChange {revision, head_commit, main_commit}` | Reserved: 501; then 202 | `not_implemented` |
| `POST /changes/{id}/revert` | `RevertChange {resolution_seq, merge_commit, main_commit}` | 202 | `not_found`, `wrong_state` |
| `GET /changes/{id}` | none | Reserved: 501; then 200 `ChangeView` | `not_implemented` |
| `GET /history?after_seq=&limit=&through_seq=` | none; defaults 0, 50, null; limit 1–100 | Reserved: 501; then 200 `HistoryPage` | `not_implemented` |
| `POST /queue` | `EnqueueTask` | Reserved: 501; then 201 `QueuedTask` | `not_implemented` |
| `GET /queue`, `GET /queue/{id}` | none | Reserved: 501; then 200 `QueueSnapshot` / `QueuedTask` | `not_implemented` |
| `POST /queue/{id}/cancel` | `QueueRevision {revision}` | Reserved: 501; then 202 | `not_implemented` |
| `POST /leads` | `StartLead {task, model?}` | Reserved: 501; then 201 `LeadCreated` | `not_implemented` |
| `GET /leads/{id}` | none | Reserved: 501; then 200 `LeadStatus` | `not_implemented` |
| `POST /leads/{id}/stop` | `StopLead {revision}` | Reserved: 501; then 202 | `not_implemented` |
| `GET /graph` | none | 200 `GraphResponse {run_id, seq, graph}` | |
| `POST /graph/rescan` | none | 202 | |
| `GET /impact?node=&depth=&change=` | none | 200 `ImpactCone`; on main, or on a change's version | `not_found` |
| `GET /tree?agent=` | none | Reserved: 501 until built; then 200 `TreeResponse {run_id, agent_id, directories, files: TreeFile[], graph_version, truncated}`, jointly capped at 2,000 entries; `TreeFile {path, hash}` has required nullable SHA-256 | `not_implemented`; when built: `not_found`, `invalid_path` |
| `GET /files?path=&agent=` | none | 200 `FileContent {path, content, hash, agent_id}` | `not_found`, `invalid_path` |
| `PUT /files` | `SaveFile {path, content, base_hash, agent_id?}` | 200 `FileSaved {path, hash}` | `invalid_path`, `stale_file`, `wrong_state` |
| `GET /events` | none | SSE, see 4.6 | |
| `GET /health` | none | 200 `Health {ok, run_id, contract_version, mode, missing_settings}`; mode is `live` or `replay` | |

- Every error is `ErrorResponse {error: {code, message}}`. The codes are `not_found`,
  `wrong_state`, `slot_busy`, `not_configured`, `spend_brake`, `invalid_path`, `stale_file`,
  `not_implemented`, `invalid_request`, `internal`, `forbidden` and `too_large`. They map to 404, 409, 409, 503, 403, 422,
  409, 501, 422, 500, 403 and 413. Only `not_configured` adds `missing: list[MissingSetting]`, where
  `MissingSetting {name: SettingName, problem: "missing" | "invalid", slots: list[Slot]}`.
  `SettingName` is `META_API_KEY`, `BASETEN_API_KEY`, `OVERSIGHT_BUDGET_USD`,
  `MUSE_SPARK_INPUT_USD_PER_M` or `MUSE_SPARK_OUTPUT_USD_PER_M`; values are never sent.
  Health's `missing_settings` defaults to an empty list (contract 1.2, approved 2026-10-27).
  FastAPI's own validation error is replaced by this format, and so is an unexpected server-side
  failure, as `internal` (contract 1.1, approved by the maintainer on 2026-10-26).
- `PUT /files` writes main, committed at once with the user as author, or the named agent's
  worktree, only while that agent is idle in review. `base_hash` is the SHA-256 of the content the
  user started from: if the file changed since, for example through a merge, the save fails with
  `stale_file` instead of overwriting it. The rule is the same for main and for the new side of a
  review, in an agent's worktree.
- Approve takes no hash: in review the agent is idle, so the only later edits are the user's own.
- The static replay site has no backend: its recorded-run label comes from its build mode, not
  from `/health`.

### 4.8 Generation and checks

- `make contracts` writes `contracts/generated/oversight.schema.json` from the Pydantic models.
  Events and responses use the serialization schema; requests use the validation schema. Then
  json-schema-to-typescript 16.0.0 writes `contracts.ts`, with `additionalProperties: false` and
  unreachable definitions included. The script appends `CONTRACT_VERSION` and a "generated, do not
  edit" header.
- `make check` regenerates both files and fails on any byte difference. TypeScript, the generator and
  its formatter are pinned through `pnpm-lock.yaml`.
- A backend test validates every line of every log in `fixtures/events/` with
  `TypeAdapter(ServerEvent)`, because the generated types validate nothing at run time. It also
  fails on any absolute path in a log.
- `make check` fails when a branch whose name does not start with `contracts/` changes anything
  under `contracts/` (`tools/check_contract_scope`).

### 4.9 What freezes, and how a change is proposed

- **Frozen on approval, as contract 1.0.0:** the envelope and the eleven events; the node, edge and
  other id formats; the commands with their bodies, statuses and error codes; the SSE framing and
  the resume rules.
- **A minor version (1.x)** adds without breaking: a new field with a default, so old fixtures
  still validate; a new endpoint; a new event type, which the reducer's exhaustive switch then
  forces the UI to handle. Renames, removals and type changes would be 2.0; they are avoided during
  the hackathon.
- **A new enum value, approved by the maintainer, 2026-10-29:** a new value in an existing enum (a
  `Literal` in the contract models) is also minor when every existing payload and recorded fixture
  still validates unchanged and every consumer of that enum is exhaustive, so a checker or a test
  forces each consumer to handle the new value, as the compiler does for a new event type. A
  TypeScript consumer is exhaustive with a switch with a `never` check, or a `Record` keyed by the
  enum. A Python consumer is exhaustive with a `match` that ends in `assert_never`, or an explicit
  table keyed by the enum, checked by pyright, which the repository already runs; where pyright
  cannot check one, such as a table's completeness or code outside its strict scope, a test fails on
  an unhandled value. A consumer that is not exhaustive is made exhaustive in the same change, or
  the change is not minor. Code that decides a merge, a rule result or whether a change waits fails
  closed: an unknown value means the change waits. Strict models stay as they are: extra fields
  remain forbidden, and a new field still needs a default. Removing or renaming a value stays 2.0.
  The maintainer added the Python consumers and the fail-closed rule later the same day.
- **Bounded exception, approved by the maintainer, 2026-10-27:** 1.4.0 extends reserved
  enums and requires observed `revision` and `head_commit` in Approve, Reject and Request
  changes. Approve and Request changes require a positive revision; Reject alone accepts zero,
  guarded by the agent's recorded base rather than current main. Historical events still load
  without provenance; live resolutions explicitly name their principal and head. Requests and
  TypeScript consumers must migrate together. 1.4.0 keeps `SaveFile.base_hash` a required string.
  The separately delivered 1.9.0 makes it required and nullable only together with safe creation,
  zones and inventory. The maintainer moved that bundle from 1.7.0 to 1.8.0 on 2026-10-29, when
  standing grants took 1.7.0, and moved it again on 2026-10-30, when E6 and item 11's plans took
  1.8.0. No alternate schema may reuse any of these version strings, including 1.4.0. These approved
  changes are exceptions to the ordinary minor-version rule, not a general relaxation.
- **To propose a change:**
  1. The front that needs it stops and writes a short request: what, why, the modules it affects,
     and whether it is compatible.
  2. The maintainer decides.
  3. The coordinator makes one PR on `contracts/<topic>`: the models, the version, `make contracts`,
     the fixtures, and a line in DECISIONS.md.
  4. `make check` passes, the maintainer approves the change, and the coordinator merges it.
  5. Open branches merge main.

## 5. Modules in order

Each front is one worktree, one branch and one PR; it is done when its command passes on the merged
result. The days assume the build period starts on Tuesday at 01:00. The owners are roles; section
7 says who fills them.

| # | Front | Day | Needs | Owner | Done when |
| --- | --- | --- | --- | --- | --- |
| 1 | Skeleton and tooling | Tue | approval | coordinator | `make setup && make check` passes on the empty skeleton, `make dev` serves a blank page through the proxy, and the commit-msg hook rejects a bad subject and an oversized commit |
| 2 | Contracts v1 | Tue | 1 | coordinator; the maintainer approves | `make contracts && git diff --exit-code contracts/generated` and `uv run pytest contracts` |
| 3 | Event store, SSE, fake player, hand-written log | Tue | 2 | strong-tier subagent | `uv run pytest backend/tests/events` (seq strictly increasing under two writers, resume after Last-Event-ID, every fixture valid), and `make replay` streams with `curl -N` |
| 4 | Smoke test | Tue | 3 | support | `make smoke` exits 0 from a clean clone |
| 5 | Docs | Tue | 2 | coordinator | ARCHITECTURE.md, DECISIONS.md, the README section and the DEVLOG line, reviewed by the maintainer |
| 6 | API probe | Tue | key and credits | backend implementer, on the maintainer's OK | `uv run python tools/probe_api.py meta`, then `baseten`, writes `docs/api-probe.md`; it decides native tools or the JSON fallback, and agent B's route |
| 7 | Demo repo | Mon | none | backend implementer; merged in PR #1 | `make demo-repo-check`: its tests pass in its own environment |
| 8 | Graph core | Tue to Wed | 2, 7 | strong-tier subagent | `uv run pytest backend/tests/graph`: one test per graph rule, a golden snapshot of the demo repo, deltas, change graphs, and the cone's limits and omitted counts |
| 9 | Frontend foundation | Tue to Wed | 2, 3's log | frontend | `pnpm -C frontend test` (the reducer over the fixture logs) and `pnpm -C frontend build` |
| 10 | Graph view | Wed | 9 | frontend | vitest on the state-to-elements mapping, and `make replay` shows the preset layout; taxi edges tried on Tuesday |
| 11 | Workspace and git | Wed | 2 | strong-tier subagent for the plan, reviewed first; backend implementer | `uv run pytest backend/tests/workspace`: worktree lifecycle, escaping paths and symlinks refused, timeouts kill the process group, pytest results parsed |
| 12 | Agent runtime | Wed | 6, 8, 11 | strong-tier subagent for the loop, backend implementer for the tools | `uv run pytest backend/tests/agents`: a whole task through the fake model, the edit_file rules, the no-progress stop, the step limit |
| 13 | Agent card and live diff | Wed | 10 | frontend | vitest on the card and diff states over the fixture logs |
| 14 | Playwright over replay | Wed to Thu | 13 | frontend or support | `make e2e` checks level 1's definition of done on a log |
| 15 | Change manager, level 1 | Thu | 8, 11, 12 | strong-tier subagent; plan reviewed first | `uv run pytest backend/tests/changes`: propose, approve as a merge, reject as a discard |
| 16 | API and wiring | Thu | 3, 12, 15 | backend implementer | `uv run pytest backend/tests/api backend/tests/e2e` through the fake model, then `make smoke` |
| 17 | Review mode with Approve and Reject | Thu | 13, 16 | frontend | `make e2e`: approve updates the graph; no key approves |
| 18 | Level 1 integration | Thu | all | coordinator with the maintainer | one real run recorded to `fixtures/events/level1.jsonl`, and `make demo` three times in a row from a clean start |

**Critical path to Thursday:** 1 → 2 → 3 → 11 → 12 → 15 → 16 → 18. Graph core (8) feeds the agent
runtime's focus and the change manager. The frontend runs beside it on 2 and 3's hand-written log.
Fronts 6 and 7 start as soon as Tuesday starts: the probe decides how front 12 talks to the model,
and the graph core's golden test needs the demo repo.

The level 1 change manager stays small: with one agent, main never moves before an approval.
Testing the combined result, conflicts, Request changes and user edits come at level 2.

**Level 2 (Friday to Sunday)**, extending the same modules:

- the second agent, on Baseten if the probe passed;
- the cone in the UI, highlighted and listed, with what was omitted;
- pending changes drawn dashed in the agent's color;
- Request changes and the `revising` state;
- tests on the combined result when main moved;
- the editable new side, and file-view edits on main;
- the spend meter and the brake;
- stop and discard;
- a robustness pass;
- the two-agent demo, three times in a row.

**Level 3** starts only if levels 1 and 2 are solid on Monday, November 2.

## 6. Code rules

These cover what AGENTS.md leaves open.

1. **Branches and commits.** Branches are `<module>/<topic>`, such as `graph/imports` or
   `frontend/review-mode`. Commits follow Conventional Commits with the module as scope:
   `feat(graph): resolve relative imports`. The scope may be left out for changes outside a module,
   and the subject fits in 72 characters. A commit-msg hook checks both.
2. **Pinned versions.** Python dependencies are pinned with `==` and `uv.lock` is committed. Node
   packages use exact versions and `pnpm-lock.yaml` is committed; Node 24 and pnpm are pinned in
   `package.json`, and Python 3.12 in `.python-version`. A release is adopted only once it is at
   least 14 days old, enforced by uv's `exclude-newer` and pnpm's `minimumReleaseAge`; an exception
   names the package and its reason in DECISIONS.md.
3. **New dependencies.** A new dependency needs a license check (MIT, Apache-2.0, BSD or ISC; SIL OFL
   1.1 for fonts) and a line in DECISIONS.md; `make audit` checks every resolved package, transitive
   ones included. Only the coordinator edits manifests and lockfiles, one change at a time.
4. **Generated files.** Contract output lives only in `contracts/generated/` and only
   `make contracts` writes it.
5. **Fixtures.** Event logs live in `fixtures/events/`, one envelope per line. Recorded runs are
   exported from the store and never edited; hand-written logs are named `handwritten-*`. Replay,
   vitest, Playwright and the static site read the same files.
6. **Tests.** Each module ships its unit tests in the same PR. The backend is tested end to end
   through the fake model. `make check` runs with the API key variables removed from the
   environment, so a test cannot reach a real model.
7. **Determinism.** One clock and one id source, both injectable. Fake-model runs give the same log
   apart from `ts`, which allows golden tests. The runtime workspace's first commit uses a fixed
   author and date, so main's base hash is the same in every take.
8. **Async.** No blocking call in async code. Subprocesses run only through the workspace runner,
   with a timeout and a kill of the process group. Git runs with `GIT_TERMINAL_PROMPT=0` and a fixed
   author for agent and user commits; never stash, never rebase.
9. **Types.** Pyright strict on contracts and backend. TypeScript strict, with no `any` and
   exhaustive switches over event types.
10. **Frontend.** Cytoscape and Monaco instances never enter the store: their owning components keep
    them. Colors come only from theme tokens. Text is English, in sentence case.
11. **Secrets.** Read once by `settings.py`, never logged. `.env.example` lists names only.
12. **Size.** A PR is one front, up to about 400 changed lines without generated files, fixtures
    and lockfiles. A commit has at most 1,000 changed lines, added plus removed, counted the same
    way. A commit that cannot be split says why in a `Commit-Exception:` trailer; merge commits are
    exempt.
13. **New areas.** No new top-level directory or backend module without the maintainer's OK, so the
    import contracts keep their meaning.
14. **Hand-backs.** Every worker reports in one fixed format, judged by its diff and a rerun, never by
    its own words:

```text
STATUS: ready_for_review | blocked | failed
Changed: <commits>
Verified: <command> -> <result>, one line each
Not verified: <what, and why>
Open: <questions, risks, follow-ups>
Still running: <none, or the processes it could not stop>
Details: <path, for anything longer than about 2,500 characters>
```

15. **Claims.** README.md, ARCHITECTURE.md and the video state speed or size only as measured on the
    demo repo, with the command that measured it.

## 7. Development flow

**Roles.** The coordinator sets the routing and the effort per task, and keeps them with machine
details outside this repository.

| Role | Who |
| --- | --- |
| Decides the critical questions and owns `contracts/` | the maintainer |
| Coordinator: plans fronts, writes briefs, verifies hand-backs, approves and makes merges; builds the skeleton and the contracts, then writes no product code, only manifests, lockfiles, docs and merges | Claude Code (Opus) |
| The critical path and the hard backend parts: the event bus, graph core, the agent runtime, the change manager | Claude Opus subagent |
| Bounded backend tasks: endpoints, glue, tests | Claude Sonnet subagent |
| Frontend: graph view, Monaco, panels | Codex |
| Support and mechanical steps: test additions, smoke scripts, surveys, formatting, reruns; no design and no new module | GLM; Claude Haiku or Sonnet when GLM is out of quota |
| Independent review | a model from another provider than the author's: Claude reviews Codex's changes, Codex reviews Claude's |

- Muse Code (Muse Wonder) contributed bounded implementations, tests, tooling fixes and development plans, and later
  reviewed the coordinator's Codex implementation loops. Development use runs under the maintainer's subscription with
  its own stored credential, separate from the product key and hackathon credits. The Meta API is called only from
  inside Oversight: its agents, the API probe and deliberately recorded demo runs.
- At least four writers run at once, at least one per lane: the critical path, the frontend,
  bounded backend work, and support. Helpers are the first to stop when usage runs ahead of pace. Every headless run states its permission mode and turn limit.
- Every worker stays inside its own worktree and is launched confined where its tool allows. Review
  looks for code that reads as if it came from elsewhere.

**Review.**

1. The worker hands back in the fixed format.
2. The coordinator reruns the decisive claims (`make check`, the front's command) on the branch.
3. A reviewer from the other provider checks correctness against the brief and the contracts, and
   ranks each finding BLOCKING, SHOULD-FIX or NIT, with its file and line.
4. Each BLOCKING or SHOULD-FIX finding goes back to its front as a fix round, to a fresh agent; the
   coordinator never commits fixes on a worker's branch. A NIT does not block: it goes to the state
   record as a follow-up. A re-review covers the fix's changes and the findings still open.
   A SHOULD-FIX states a concrete failure scenario; wording, style and consistency between documents
   are NITs. For frontend views and docs, a SHOULD-FIX off the demo path (what the level's demo
   shows or runs) also becomes a follow-up front, opened at the merge; security, authority,
   lifecycle, gates and contracts keep the full rule (added 2026-10-26, the maintainer).
5. The coordinator approves the merge once the checks pass on the merged result and the review has
   no open BLOCKING finding and no open SHOULD-FIX that step 4 does not turn into a follow-up; its
   own changes always get Codex's review.

The maintainer makes the critical decisions: contracts v1 and any later contract change, spending
Meta credits, scope and level cuts, the demo script, a new top-level directory or backend module,
and publication, tags and GitHub settings.

Changes to lifecycle, deletion or gates start with a one-page plan reviewed before any code: the
workspace reset, worktree removal, the change manager's merge and discard, path validation, and the
contract-scope check.

**Integration.**

- Development worktrees live in a sibling directory, `../oversight-worktrees/<branch>`, so the dev
  server and searches do not walk them.
- Each front is a GitHub PR: the coordinator pushes its branch when it is ready for review.
- A branch that falls behind merges main.
- Before asking for approval: `make check`, plus `make e2e` for frontend fronts, on the merged
  result. The merge commit names what passed in a trailer, such as `Checked: make check, make e2e`;
  if main moved after the check, the check runs again.
- Merges into main are `--no-ff`, so each front shows in the history, and the coordinator pushes
  them once approved.

**Controls.** Each one is cheap and closes a gap that review alone leaves open. A hook can be
skipped, and a skipped check is not a pass, so the record that counts is the coordinator's rerun on
the merged result.

| Control | Where | When |
| --- | --- | --- |
| Commit messages: Conventional Commits, subject up to 72 characters | a commit-msg hook; pre-push checks every commit being pushed again | front 1 |
| Commit size: at most 1,000 changed lines, or a `Commit-Exception:` trailer (rule 12) | the same commit-msg hook, since it reads the trailer; pre-push checks again | front 1 |
| `make check` before anything reaches GitHub | a pre-push hook: until GitHub Actions runs it, the only check before a push | front 1 |
| Hardened CI: actions pinned by SHA, read-only permissions per job, `persist-credentials: false`, a timeout per job | `.github/workflows/ci.yml` | front 1 |
| A release age of 14 days | uv and pnpm settings (rule 2) | front 1 |
| Licenses of every resolved package, and known vulnerabilities | `make audit`; it needs the network, so it stays out of `make check` | by Sunday, then whenever a lockfile changes |
| Each control fails when it should | the hand-back that adds a control shows it rejecting one deliberate violation: a bad commit message, a forbidden import, `subprocess` outside the runner, an edited generated file, a contract change on another branch | as each control lands |

When the repository goes public, with the maintainer's confirmation for each GitHub step: first
gitleaks over the whole history and `make audit`; then a ruleset on main that blocks force pushes
and deletion, and a tag on the submitted commit.

Left out, because they cost more than they return in nine days:

- **Squash merges for a linear history.** `--no-ff` keeps every commit with its date, which shows
  the judges what was built during the event, and `git log --first-parent main` reads as linear.
- **SECURITY.md.** Nothing runs as a service on the internet: the product runs locally and the
  public site is static.
- **A reviewed plan before the hooks.** They delete nothing, and each is shown failing once.
- **Required status checks.** Only once GitHub Actions runs.

**Daily rhythm (ART).**

- **Morning, 15 minutes:** the maintainer reads the night's report and settles the critical
  decisions it raises.
- **Day:** fronts run in parallel; the coordinator verifies hand-backs as they arrive and merges
  what passes.
- **Evening:** only long unattended work, such as test runs and bounded refactors.
- **Night:** run `make smoke` and the level's demo on main, add the DEVLOG line, and send the
  maintainer a short report: what merged, its evidence, and a screenshot or a clip of the UI.

**Makefile.**

| Target | Does |
| --- | --- |
| `make setup` | installs the Python and Node dependencies and the git hooks: pre-commit, commit-msg and pre-push |
| `make dev` | FastAPI with reload and the Vite dev server, one origin through Vite's proxy |
| `make check` | ruff, pyright, import-linter, pytest, the contract drift and scope checks, tsc, eslint, prettier, vitest, gitleaks; no credits |
| `make contracts` | regenerates `contracts/generated/` |
| `make replay LOG=...` | the backend's fake event player serving a log over SSE, with the dev server |
| `make demo` | a clean workspace and the built frontend served by FastAPI on one port, with the real models from `.env` |
| `make demo-fake` | the same with the scripted fake model, spending no credits |
| `make demo-repo-check` | runs the demo repo's tests in a scratch environment outside the template |
| `make smoke` | starts the backend, checks `/health` and `/graph`, stops it |
| `make e2e` | Playwright over replay |
| `make site` | the static replay site with a bundled recorded run, labeled as one by its build mode |
| `make probe` | the API probe; spends credits, never part of `make check` |
| `make audit` | the licenses of every resolved package against the allowed list, then `pip-audit` and `pnpm audit`; needs the network |

**Pins (from PyPI and npm on 2026-10-26).**

- Backend: Python 3.12, fastapi 0.162.0, uvicorn 0.54.0, pydantic 2.14.1, openai 3.38.0.
- Backend tooling: import-linter 2.15, ruff 0.17.2, pyright 1.1.414, pytest 9.1.1, httpx 0.28.1.
- Frontend: Node 24, react 19.3.1, vite 8.3.5, zustand 5.0.16, cytoscape 3.34.4,
  cytoscape-fcose 2.2.0, monaco-editor 0.57.0, @monaco-editor/react 4.7.0, tailwindcss 4.3.3,
  lucide-react 1.61.0, and the Atkinson Hyperlegible Next and Mono packages from Fontsource 5.3.0.
- Frontend tooling: typescript 6.0.3, json-schema-to-typescript 16.0.0, vitest 5.0.5,
  @playwright/test 1.64.1, eslint 10.13.0, prettier 3.9.10.

TypeScript stays on 6.0: typescript-eslint 8.72 supports up to 6.0, not the native 7.0. The
generator's latest release is 16.0.0, so the 16-or-17 question is settled.

These are the latest releases. Rule 2's release age moves eleven of them back when front 1 locks,
all published after 2026-10-13: fastapi, openai, ruff, react, vite, zustand, cytoscape,
lucide-react, vitest, @playwright/test and eslint. FastAPI 0.143.0, the newest that qualifies,
already has `fastapi.sse`, and typescript-eslint 8.71.2 accepts TypeScript 6.0.

## 8. Where this proposal settles or departs from AGENTS.md and the plan

The maintainer answered every row on 2026-10-26: all but row 5 are approved, and his notes after the
table are part of the decision. The sections above already follow them.

| # | Proposal | Instead of | Why |
| --- | --- | --- | --- |
| 1 | The UI streams the run's log from `seq` 1, and a gap reconnects from the last applied `seq` | AGENTS.md: read `GET /graph` first, then apply later events; fetch a snapshot on a gap | Agent and change state exist only in events: a tab reloaded mid-demo would lose its cards and the change waiting for review. `GET /graph` stays. |
| 2 | SSE messages carry no `event:` field; the type travels in the JSON | unspecified | A named SSE event skips `onmessage`, a known trap |
| 3 | Containment is `parent_id`; no `contains` edges | the plan's edge kinds include `contains` | One encoding of one fact |
| 4 | `agent.status` carries the slot (`A` or `B`); the theme maps it to a color | the plan: a color | Color is presentation, and the letter is the identity |
| 5 | Stopping an agent closes its change through `agent.status`, without `change.resolved` | unspecified | Its decisions stay the three of AGENTS.md |
| 6 | `list_files` sends no `agent.focus` | "read tools also emit agent.focus" | It reads no source and names no node |
| 7 | An edit sends `change.updated`, then `agent.focus`, then `agent.step` | unspecified | The UI knows a new node before a focus names it |
| 8 | `change.proposed` carries a test run the server makes on the proposed commit | "the test result" | Evidence, not the agent's word; the agent may not have run the tests after its last edit |
| 9 | Test modules are nodes with `is_test`, hidden by default and left out of cones | unspecified | The "no test calls it" badge needs them; the screen does not |
| 10 | Commands answer 202 and their results arrive as events; `POST /agents` answers 201 with the ids | unspecified | One path for state: the stream |
| 11 | `PUT /files` carries `base_hash` and fails with `stale_file` | unspecified | A save from the file view must not overwrite a merge |
| 12 | `GET /health` added | not in the list | Readiness for `make dev` and the smoke test, and the replay label |
| 13 | `fixtures/` and `tools/` added at the top; generated TypeScript in `contracts/generated/` | the tree in AGENTS.md | Shared test data in one place; contract output kept with its source |
| 14 | `path` everywhere, including nodes | the plan: `file` on nodes | One name for one thing |
| 15 | The demo's preset layout lives in `fixtures/layouts/` and reaches the UI in `graph.snapshot` | agreed to travel in the snapshot; the file's place was open | It stays outside the template, which agents can list |

Answers:

- **1:** yes. The log is per run; each `make demo` starts a new run.
- **3:** yes. The plan's edge kinds change to match.
- **5:** no. Every change closes with `change.resolved`, so the reducer and the history have one
  closing path. Stop and discard sends `agent.status` with `stopped` and `change.resolved` with
  `rejected` and the reason `stopped`. A run that ends on its own (step limit, stall cutoff or
  error) leaves its change open for review, with the reason in `agent.status`.
- **6:** yes. A search sends `agent.focus` for its first 5 matching nodes only.
- **8:** yes, with a timeout, through the same runner as `run_tests`.
- **9:** yes. The review panel can still show how many tests reach the change
  (`ImpactCone.tests_reaching`).
- **10:** yes. 202 only means accepted; a command that cannot be accepted fails at once with a 4xx
  in the single error format.
- **11:** yes. The same rule applies to edits of the new side in an agent's worktree.
- **12:** yes. The static replay site has no backend, so its recorded-run label comes from its
  build mode, not from `/health`.
- **14:** yes. Paths are always relative to the workspace root, with forward slashes, never
  absolute: the replay logs will be public.
- **2, 4, 7, 13 and 15:** yes.

## 9. Open questions

- **Monday's work:** does it count? The answer from #questions moves front 1 to Monday night or
  keeps it at Tuesday 01:00.
- **The API:** tool calls, parallel tool calls, ignored parameters, usage reporting, the reasoning
  setting, rate limits, and the same on Baseten. Front 6 answers them before front 12 talks to a
  model.
- **Zoom levels:** the demo repo fits on one screen without them. I propose them for level 3.
