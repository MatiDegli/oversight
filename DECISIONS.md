# Decisions

Each decision with its reason. The design approved before the build is in
[docs/proposal.md](docs/proposal.md); this file adds what is decided while building.

## Dependencies

Every direct dependency, its license, and why it is here. Versions are pinned exactly, and a release
is adopted only once it is 14 days old (proposal, rule 2). Licenses were read from PyPI and npm on
2026-10-26; `make audit` will check the transitive ones.

| Package | Version | License | Why |
| --- | --- | --- | --- |
| fastapi | 0.143.0 | MIT | the HTTP API, with its own SSE support (`fastapi.sse`) |
| uvicorn | 0.54.0 | BSD-3-Clause | the ASGI server |
| openai | 3.29.0 | Apache-2.0 | the official SDK, pointed at Meta's and Baseten's endpoints (AGENTS.md) |
| pydantic | 2.14.1 | MIT | the contracts' source of truth |
| ruff, pyright, pytest, import-linter, httpx | 0.17.0, 1.1.414, 9.1.1, 2.15, 0.28.1 | MIT, MIT, MIT, BSD-2-Clause, BSD-3-Clause | lint and format, types, tests, import contracts, the test client |
| pytest-timeout | 2.4.0 | MIT | a time limit per test: a hang fails alone, with its name |
| pytest-xdist (and execnet 2.1.2) | 3.8.0 | MIT | runs the suite on 8 worker processes: `make check` waits for the slowest group, not the sum |
| react, react-dom | 19.3.0 | MIT | the UI |
| zustand | 5.0.15 | MIT | the store around the pure reducer |
| cytoscape, cytoscape-fcose | 3.34.3, 2.2.0 | MIT | the graph view and its layout |
| monaco-editor, @monaco-editor/react | 0.57.0, 4.7.0 | MIT | the editor and diff view, loaded from the local package |
| lucide-react | 1.55.0 | ISC | icons |
| @fontsource/atkinson-hyperlegible-next, -mono | 5.3.0 | OFL-1.1 | the text and code fonts |
| typescript | 6.0.3 | Apache-2.0 | 6.0, not 7: typescript-eslint supports TypeScript below 6.1 |
| vite, @vitejs/plugin-react, tailwindcss, @tailwindcss/vite | 8.3.4, 6.1.2, 4.3.3, 4.3.3 | MIT | the build, React support, styling |
| eslint, @eslint/js, typescript-eslint, eslint-plugin-react-hooks, globals | 10.12.0, 10.0.1, 8.71.2, 7.1.1, 17.13.0 | MIT | lint and the frontend's module boundaries |
| prettier, vitest, @playwright/test | 3.9.10, 5.0.4, 1.64.0 | MIT, MIT, Apache-2.0 | format, unit tests, the replay test |
| json-schema-to-typescript | 16.0.0 | MIT | generates the TypeScript contracts |
| @types/react, @types/react-dom, @types/node | 19.3.0, 19.3.0, 24.19.2 | MIT | types |

## Tooling

- **Ports:** the backend listens on 8710 and Vite on 5710, away from the defaults other local
  projects use; `OVERSIGHT_PORT` changes the backend's, and Vite's proxy follows it.
- **`tools/` may use `subprocess`:** development scripts run git once and exit, outside any event
  loop. The ban stays on the product, apart from the workspace runner.
- **Ruff skips Markdown:** its formatter rewrote the Python sketches in `docs/proposal.md`, which are
  not code.
- **Every test run has a soft and a hard cap on memory and a bound on time, and `make shots` a
  memory cap on Chrome.** Incident, 2026-10-26, 22:04 UTC: a mutation check on the agent runtime
  turned off the six-failure rule, the tool loop then spun on model replies without tool calls and
  kept growing its history, and a pytest reached 30 GB, all of the WSL VM's memory, until the
  kernel's OOM killer ended it. Earlier that day a headless Chrome had reached 13.7 GB and a python
  28.8 GB. A runaway test must fail alone, quickly, with its name, and never take the VM to its
  limit.
  - *Memory, soft layer:* `tools/memwatch.py`, loaded by the root `conftest.py`, runs a daemon
    thread that reads the VmRSS of its own process, and of no other (`/proc/self`, every 0.5 s).
    Past `OVERSIGHT_TEST_MEM_GB` GiB (8 by default; fractions allowed, so tests can use a small
    cap) it writes one line naming the running test to the terminal's stderr and exits with code
    70. It signals no other process and sums nothing over children: an earlier version stopped and
    killed descendants by pid, which could hit a process that had reused the pid of a descendant
    that was gone, and summing the RSS of processes counted shared pages once per process (451 MiB
    counted for 150 MiB of real use), so the cap fired falsely. It is portable, for a worker's bare
    `uv run pytest` and for CI, but a thread needs the GIL: one allocation in C that holds it can
    fill the VM before the thread looks, and a child that outgrows the cap is not seen. No
    `RLIMIT_AS`: it caps address space, which Node/V8 (the ESLint test) and thread arenas reserve
    far beyond what they use, so it would fail for the wrong reason.
  - *Memory, hard layer:* `make check` runs pytest through `tools/capped.sh`, in `systemd-run --user
    --scope -q -p MemoryMax=10G -p MemorySwapMax=0` when that works here, and as it is when it does
    not (CI, sandboxes, no user manager). The kernel accounts memory per cgroup, a shared page once,
    for pytest and everything it starts, and ends the biggest process of the scope alone, whatever
    holds the GIL. 10G sits above the 8 GiB of the soft cap, so a runaway Python test is normally
    ended by the soft one, with its name, and the scope is the net under it. The probe carries the
    same properties as the real call, so a manager that refuses them gets the fallback. A bare
    `uv run pytest` has only the soft layer.
  - *Time:* pytest-timeout, `timeout = 120` in `pyproject.toml`, signal method, so only that test
    fails. Measured on 2026-10-26: the slowest test takes 8 s warm and the demo environment builds
    in under 1 s cold from an empty uv cache on this machine; 120 s leaves room for a slow CI runner
    and still ends a hang within two minutes. `timeout_func_only` keeps fixture setup outside it,
    since a cold run on a slow network may spend longer than that building the demo environment
    (its uv steps carry limits of their own, 300 s each, as does the ESLint run). A test that needs
    more says so with `@pytest.mark.timeout(seconds)`. That leaves setup and teardown unbounded (a
    child that never opens a FIFO can block the executor's shutdown), so `make check` also runs the
    whole pytest run through `tools/bounded.sh`, which wraps it in `timeout --kill-after=30 900`:
    after 15 minutes it ends the process group, and 30 s later kills it, and one `TIME BOUND:` line
    on stderr names the bound, so a CI log and a local run both say what happened instead of a bare
    124 or 137 (a status of 124 or 137 from the command itself, before the bound, gets no line).
    900 s and not 1200: GitHub cancels the CI job at 20 minutes, install and lint come first, and a
    cancelled job prints nothing. `RUN_TIMEOUT` overrides the 900 for one run.
  - *Chrome:* `frontend/shots/capped.sh`, the one helper `make shots` uses for the capture and the
    detector, runs the command under the same helper with a limit of 6G, so the two caps share one
    piece of logic.
- **`make check` runs its groups at once, and pytest on 8 xdist workers** (2026-10-27). Measured on
  main, warm, one step after another: ruff, import-linter and contract drift under 1 s each,
  pyright 5.3 s, pytest 81.3 s (665 tests), tsc 2.4 s, ESLint 4.8 s, Prettier 1.0 s, the frontend's
  vitest and node tests 11.1 s, gitleaks 0.3 s: 106 s in all, and pytest was three quarters of it.
  After, five runs in a row, all green: 24.3, 26.3, 24.7, 25.7 and 25.9 s, with the suite in 19 s.
  Three changes, none touching a gate's content:
  - *Groups.* `check` starts python, contracts, frontend and secrets with `make -j4
    --output-sync=target`: each target's output comes out in one piece when it ends, so groups
    never interleave. Inside a group the steps stay in order, lint before tests, so a lint error
    ends the group before its tests start. A failure leaves the jobs already running to finish,
    starts no other, and `make` exits 2: the first failure still fails the whole target (tried
    with a lint error and with a failing test). CI runs `make check-ci`, the same without
    gitleaks, which its check job has not got (the history is scanned in a job of its own), so
    the two cannot drift apart.
  - *xdist.* Chosen over splitting the suite by hand because the tests were already isolated:
    real uvicorn on a socket bound before it starts, `tmp_path` runtimes, no shared state.
    `-n 8` is fixed, not `auto`, so the memory share below is the same on every machine;
    `WORKERS=0` runs one process, and `-p no:xdist` and a bare `uv run pytest` still work (serial,
    82 s). The final code ran the whole suite under xdist 11 times, six of them `make check` and
    five bare, at 4, 16 (twice) and 32 workers and with a budget of 4 GiB (half a share), without
    one failure. The scheduler is `load` (balance over locality):
    a session fixture, the demo environment, is built once per worker that needs it, under a
    second each.
  - *The ESLint boundaries test* (`tools/tests/test_eslint_boundaries.py`) writes probe files into
    `frontend/src` and lints them with the real configuration. Run beside tsc, ESLint and Prettier
    it would make them fail on a probe, and two workers would race on the same folders, so it is
    excluded from the parallel python run and runs alone in the frontend group, after the frontend
    gates (a test in `test_capped.py` pins that each file is in exactly one group). Linting the
    probes from stdin does not work: typescript-eslint's project service needs the file on disk.
  - *The caps reach the whole worker tree.* The workers are children of the controller that
    `bounded.sh` and `capped.sh` start, so they share its process group (the outer 900 s ends
    them) and its systemd scope (the 10G counts them); a test runs a nested xdist pytest and
    compares both. memwatch gives each worker an equal share of `OVERSIGHT_TEST_MEM_GB` (1 GiB
    each at 8 workers), so the workers together stay inside the budget where no scope exists;
    the idle controller keeps the whole cap. pytest-timeout runs in each worker (the nested test
    checks it fails one test and the rest run). A worker that memwatch ends is a crashed worker
    to the controller, and `--max-worker-restart=0` ends the run there, so a runaway test is not
    run again on a fresh worker.
- **The frontend's lint is type-aware** (`strictTypeChecked`), with the exhaustive-switch rule on,
  so a new event type fails the build until every switch handles it.
- **Impeccable's scan reads the replay site, not the live page** (2026-10-27). The scan failed in
  every `make shots` run with "Navigation timeout of 30000 ms exceeded". Cause, reproduced with a
  page of one line: the detector's navigation finishes only when the page holds no connection, and
  the live page keeps `/events` open for as long as it runs, so it never finishes (a page without
  the stream finished in 2 s). A longer timeout would only have waited longer. The scan now loads a
  second Vite server in replay mode, serving the same log: the static site judges open, which
  opens nothing after load. Replay follows a log's own gaps, so `scan-log.ts` gives every event the
  first one's time and the page is complete at load (seq 20 at network idle, checked). The captures
  still use the live page. The scan stays optional when the detector is missing, and `make shots`
  now prints the findings, not only where the file is.

## Contracts v1

- **Named types:** ids, paths, timestamps and every shared literal are Python 3.12 `type` aliases,
  so the JSON Schema gives each a definition and the TypeScript types keep its name (`NodeId`,
  `AgentState`). Run, agent and change ids and `ts` carry a pattern; node and edge ids do not, since
  the graph core's tests own their format.
- **Rules a schema cannot state are validators:** an edge's `reason` is set exactly when it is
  possible, and a resolution's `merge`, `merge_commit` and `conflict` agree with its decision.
- **What json2ts receives:** fields keep no title, and a `$ref` keeps no description beside it;
  otherwise json2ts declares a type per titled field and a second type per described reference
  (`ChangeGraph1`). Those field descriptions stay in the Python source. The generator also drops
  the schema's empty root type and json2ts's per-definition note.
- **`oversight_contracts` re-exports only `CONTRACT_VERSION`:** the names live in `common`,
  `graph`, `events` and `commands`, so each import says which part of the contract it uses.
- **The contract-scope check** (docs/proposal.md, section 4.8) comes in its own small PR, with its
  plan reviewed first, since it guards the tree.
- **Contract 1.1 adds the error code `internal` (500)** (the maintainer, 2026-10-26): without it an
  unexpected server-side failure, such as the change manager failing to open, answered FastAPI's
  plain 500, outside the single `ErrorResponse` format. A minor version, since it only adds a code;
  recorded logs at 1.0.0 still validate.

- **Contract 1.3.0 reserves level 3 merge policy and two-agent plans** (approved by the maintainer,
  2026-10-27): optional fields, three events (`change.assessed`, `merge_mode.changed`, `plan.updated`)
  and seven endpoints that return 501 reserve the wire format, so a later level can add behavior
  without a breaking change. File creation moves to 1.4.0.

## Design

- **Signal lives in `DESIGN.md` at the root, with `PRODUCT.md` beside it:** the design skill
  Impeccable reads both files there, so the design reference and the skill share one source.
  `DESIGN.md` follows Impeccable's format (tokens in the frontmatter, then its eight sections), was
  written by hand from the final Signal sketches with the matte header, and records where it departs
  from them. AGENTS.md wins over both files.
- **Impeccable is pinned at 4.5.2** (Apache-2.0), released Oct 9, so it passes the 14-day rule. Its
  installer fetches the latest release, so it was copied from the signed 4.5.2 release bundle, with
  its sha256 checked, and with engine 0.1.14, whose checksum matches the release's. It lives in
  `.claude/skills/impeccable/`, outside git, with no hooks and none of its Claude agents: they serve
  only new-work and live mode, which we do not use. Its working folders (`.agents/`, `.impeccable/`)
  are gitignored.
- **Allowed commands: shape, harden, onboard, audit, critique, polish, clarify, typeset, layout.**
  They plan, check and refine inside the agreed world. **Forbidden: bolder, colorize, delight,
  overdrive, animate, live, generate and document.** The first five push toward more color, motion
  or expression than Signal allows; live and generate open visual variants outside DESIGN.md;
  document would rebuild DESIGN.md from the current theme, whose values are not the design's. shape
  plans a screen inside DESIGN.md and never opens a new visual world.
- **PRODUCT.md was written without an interview:** Impeccable's init asks the user; its answers were
  taken from AGENTS.md instead (platform web; one developer supervising Muse agents on a Python
  repository; judges through a 3-minute video and a replay; English, sentence case, dark). No live
  mode, and new surfaces are built code-first; that setting belongs in `.impeccable/config.json`,
  which is not committed.
- **Day mode is for the submission gallery only:** the video and the demo are dark, and the gallery
  shows both. It is a bounded front for Mon Nov 2, started only once level 2 is closed, otherwise cut
  like level 3. Every color comes from a token under `data-theme` on the root element, including
  what Cytoscape, Monaco and the dot-field canvas receive, so the switch repaints everything and no
  component picks a color. The screenshots (`make shots`) cover both modes.
- **Approve answers only a pointer click (maintainer, 2026-10-26):** a click with `detail === 0`,
  which is what Enter, Space and a script's click produce on a focused button, never calls approve;
  the review panel shows "Approve with a click: it merges into main" under the buttons instead.
  Approve stays focusable and labelled, and Reject stays keyboard-operable. Reason: AGENTS.md says
  Approve has no keyboard shortcut and must never fire while a comment is typed, and Tab then Space
  from the comment box would merge into main. Cost: keyboard and screen-reader users cannot approve;
  they are told why, and the merge stays a deliberate pointer act.
- **Bezier edges kept over taxi (maintainer, 2026-10-26), superseded on 2026-10-27 by the graph
  at the sketch's direction, below:** captures of both on the preset layout were compared; curves
  read better. That preset packed every module in a tight box, with no room for lanes; on the wide
  preset taxi reads better.
- **The graph at the sketch's direction (maintainer, 2026-10-27):** the merged graph was far below
  the Signal sketch, and the maintainer said that not everything has to fit on screen. It is now
  a canvas that is panned and zoomed, built to the sketch's measurements at 100% on a 1920x1080
  frame: rows of 282x46px with the name, state word and badge on one line, frames with a header
  band, classes inside modules, a 26px badge, radii of 6 and 10px. Edges are taxi: orthogonal
  with rounded corners, routed through the gutters between frames (`routes.ts`), because a curve
  between two columns of rows crosses the rows in between; the router can loop a call at the left
  of a column, which Cytoscape's own taxi cannot do for two rows of one column. Cost: Cytoscape
  has no per-side padding and no way to read the bends of an edge in a headless test, so the
  header band is built from uniform padding and a minimum size (`frames.ts`) and the bends are
  computed from the boxes and checked against the geometry. The preset is generated in two passes
  (module blocks in columns by who calls whom, then each module's contents in its block) with
  gutters of 120px and 80px, so a later zoom view can shrink a block to a dot. The camera starts at
  100% on the hub's module and never fits on its own (the fit button does); the view's edges move
  to a gutter rather than cut a frame. Import edges are kept in the data and not drawn: the sketch
  draws calls, and with 36 functions the imports doubled the lines without saying what a change
  reaches. Cost of the whole: in review, at the 12px floor, only the nearest callers of the demo's
  hub fit beside the cone card; the rest are lit and listed as out of view.
- **Answers on the graph at the sketch (maintainer, 2026-10-27):** edges at a lit node stay grey,
  and only a change's own edges take the agent's color; imports stay hidden; a cone row that says
  "Out of view" pans the camera to its node only when clicked (at the zoom it has, never on its
  own, and the card then lists what is still out of view); the start view is the module of the hub
  (pricing).
- **Fit stops at the zoom range (2026-10-27):** a graph too wide for 20% is framed at 20%, centered,
  and is not fitted below it, rather than letting Fit go below the range for its own framing and
  restoring the limit afterwards. A wheel or button zoom that started below the range would have
  jumped up to it on the first step, which reads as the opposite of the press. Every camera move
  (Fit, review framing, growth in watch mode, a reveal) stops at the range's edge about the view's
  middle and never changes the range itself. Cost: on a very wide graph Fit does not show it all.
- **Visual parity round 1 (maintainer, 2026-10-27).** Nothing moves; each answer below is built.
  Edges draw under leaf nodes and quiet down (0.45) unless an agent is at them or the person
  selects a node; frame headers can still be crossed by an edge (a frame is one node, and its fill
  would hide the edges behind it). Search, history and settings are left out of the top bar until
  each is built, with no logo mark or Ctrl K hint. The ready card shows the summary only. The Agents
  heading counts slots in use and the task box is pinned to the panel's bottom. The legend names
  "Editing or changed" (dashed and filled: editing is dashed here, solid only in the sketch). Each
  step shows its age against the latest event's time, kept per step in the state, so a replay reads
  as it did live. The budget is "US$150" when round. Module file names stay in Next. A touched frame
  says its state in its header and gives up its count while it does; an empty module or class
  stacks its title and count. If the person's focus was in what leaves a card (the step list, Live
  diff), the card takes it, and only then.
- **Review change takes its agent's color (2026-10-27, approved by the maintainer):** the sketches
  fill the button with the agent's color and the dark on-agent letters, and DESIGN.md's button list
  does not name it (primary is text-colored for Approve and merge, Send and Start agent). It
  follows the sketch: it sits on that agent's own card, beside the lamp and the status pill that
  already carry the color, and its label and eye icon say what it does. Cost: two primary looks on
  the screen. Reverting is one class (`bg-text text-page`).

- **Watch growth includes a new node only outside a panel and after navigation rests:** if an
  added node lands outside the viewport, the camera may widen to include it. It waits three seconds
  since the person's last pan, wheel zoom or pinch, and never interrupts navigation. This changes
  the camera, not node positions, and keeps new code visible without stealing focus. DESIGN.md's
  “nothing moves by itself” protects node positions the person did not cause; it does not forbid
  this guarded camera adjustment.
- **Growth relocates a module only as a last resort, and restores it:** when a new node cannot fit
  anywhere without overlapping another frame, its root module shifts as a unit, retaining its
  leaves' relative positions. The pre-shift positions are saved and restored when the additions
  that required the fallback disappear, including rejection. Other modules stay put. This is the
  bounded consequence of the code addition, not an unsolicited rearrangement or a mode change;
  restoring it preserves the person's layout and DESIGN.md's “nothing moves by itself” rule.

### Where Impeccable clashes with our rules

Ours wins in every case below.

- **Pure black.** "Never pure black" is a common rule of design skills; 4.5.2's files, as read, do
  not state it outright (its palette generator even offers pure black), but if it or its detector
  raises it, ours wins: the graph's ground is `#000000`, plain black until the dot field
  (AGENTS.md); panels use near-blacks.
- **Its core stance** ("go all out", "dream big and bold") against Signal's restraint: color is an
  agent or a state, panels are flat. Hence the forbidden commands.
- **"Dark or light is never a default; pick it from the use scene."** Ours: dark everywhere
  (AGENTS.md). The use scene is long supervision and a dark demo video; day mode is the gallery's.
- **The detector's "pulsing dot" and its craft floor's "one authored moment" of motion.** Ours: a
  working agent's status lamp pulses, the one motion on the person's surface, because it reports
  work in progress; it stops under reduced motion.
- **The detector's "colored glow on a dark background".** Ours: the dot field's light and the rings
  are agent-colored light on black, because the light says which agent works where; it is dithered
  dots on its own canvas, not a glow on an element, and comes after level 1.
- **document's process:** it extracts DESIGN.md from the code, asks before replacing one, and
  writes a sidecar (`.impeccable/design.json`). Ours: DESIGN.md is written by hand from the agreed
  design, document is forbidden, and no sidecar is committed.
- **init's interview.** Ours: PRODUCT.md answers its gaps from AGENTS.md, as recorded above.

## Process

- **Review tiers:** a SHOULD-FIX must state a concrete failure scenario, and wording, style or
  consistency between documents is a NIT. For frontend views and docs, a SHOULD-FIX off the demo
  path becomes a follow-up front opened at the merge instead of another review round; security,
  authority, lifecycle, gates and contracts keep the full rule. Reason: on day one the design
  document took four review rounds over consistency details while the critical path waited, and a
  finished graph view waited on styles it could adopt later. Recorded in docs/proposal.md, section 7.

## Model API

- **Muse Spark in Contributor mode, not Standard:** it costs less, and the code the agents see is
  the demo repository or this repository, both public and shown to Meta anyway. The API probe ran in
  Standard mode (docs/api-probe.md); its findings on tools, reasoning and usage still hold.
- **Muse Code for development (maintainer, 2026-10-26):** development agents may use Muse Code with
  Muse Wonder on the maintainer's subscription, so the work splits across more providers and
  Meta's frontier model helps build the product. It keeps its own stored credential: never the
  product's `.env` key, which an exported variable would override, and never the hackathon
  credits, so the spending brake's ledger and Meta's console total stay about the product alone.
  The product's own agents still use only Muse Spark and Muse Spark Open.

- **Structured setup readiness (maintainer, 2026-10-27; contracts 1.2.0):** missing or invalid
  provider keys, Meta budget or token prices answer `not_configured` with HTTP 503, since server
  configuration makes the requested service unavailable; 403 `spend_brake` remains for uncertain
  ledger state or spend above the limit. Health lists the same problems across offered slots, and
  `make demo` checks them before building. Details contain only setting names, problem kinds and
  blocked slots, never values, key lengths or prefixes, so the UI can explain setup without
  exposing credentials. Keys are opaque nonempty strings without whitespace; authenticity is not
  checked by calling a provider. The optional error `missing` field is omitted for other codes,
  preserving their existing wire shape; recorded 1.0 and 1.1 events remain valid.

- **Dot field brought forward (maintainer, 2026-10-27):** the dot field, rings and breathing light
  behind the graph move from "after level 1" to now, built as DESIGN.md specifies; it must never
  block level 1, so with no WebGL, a failed draw or a lost context the graph shows plain ground.

## Change manager stages

- **Split by stage behind the same facade (maintainer, 2026-10-27):** the 1,565-line manager mixes
  proposal work with authority, recovery and event delivery. Four stacked refactors introduce
  state/support, proposal, approval/merge/recovery, then delivery, without changing behavior,
  contracts or test expectations. `_state.ChangeState` is the sole mutable owner; the facade
  holds a `_support.ChangeContext` with dependencies and that state, rather than copied aliases.
  Assignable compatibility properties preserve private test access. Public signatures and tasks,
  lock/await order, combined tests, permanent main suspicion and receipt ordering remain intact.
- **Typed ports only while needed:** ports are a record of lambdas that resolve facade methods
  on each invocation, preserving class-level monkeypatches without making the facade implement a
  protocol. PR 2 adds finish_pending/send_requested for proposal; PR 3 adds drain_pending/finish
  for approval, record_merge for merge/recovery, close_out for recovery and resolution for merge.
  The resolution port exposes only its consumed conflict argument. PR 4 replaces all of these
  with direct stage calls. The permanent drain port serves delivery's three wait sites, keeping
  `_drain` static and patchable. No unused publish_main, request_changes or supersede_pending port
  is introduced; Request changes retains its existing dynamic facade dispatch.
- **Prove movement separately from scaffold review:** the data manifest lists every owner path,
  moved operation and rename, including read_facts/unsent_events and the three record classes.
  The AST checker compares operations and records while preserving callbacks, locks, awaits and
  exception boundaries; its three seeded mutations must fail. Moving a method out of its class
  changes only docstring indentation, normalized with cleandoc. Constructors, forwarding methods,
  compatibility properties, imports and port registration remain explicit reviewer scope.
  Helpers are forbidden from importing the facade, even in TYPE_CHECKING. Logger names stay
  `oversight.changes._manager`. There are no new behavioral seams or policy integrations.
- **Create-files follows this split:** it is absent from the base, so `_save_main`, `_settle_save`
  and `_restore_save` remain on the facade. Create-files must be redone over it later. The facade
  remains over 500 lines to keep the residual file and revision transactions within this agreed
  scope; no function exceeds 80 lines. Two unsuccessful fidelity/review repair rounds stop the
  sequence for a coordinator decision.

## Level 2 closed on Tue Oct 27

Level 2 closed on Tue Oct 27. Close-run logs, validation results and spending records
are not included in this repository. Level 3 wiring starts now. Contracts 1.4.0 await
the maintainer's approval.
