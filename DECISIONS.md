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

- **GET /tree in the next contract batch (maintainer, Tue Oct 27, 2026):** reserve the runtime
  workspace tree endpoint there. The shell's file navigation needs it. No contract changes here.

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

### Design rethink (maintainer, Tue Oct 27, 2026)

- **R1: module folding accepted.** Modules join classes as foldable groups. Every module starts
  as one card and opens on a read or an edit inside it; its header toggles it open or folded.
  Calls share one counted edge per directed module pair when either module is folded.
  This keeps labels readable as the repository grows without losing call direction.
- **R2: drag module frames only.** Function rows keep their column. Edges re-route on every
  animation frame during a drag. This prevents stretched frames and routes that snap on drop.
- **R3: function edges on demand.** Module edges show at rest. Function edges appear on hover,
  selection and working agents' cones; calls within one file stay hidden. This reduces clutter
  while keeping the calls relevant to the current work visible.
- **R4: local light, amended by the maintainer on 2026-10-27.** The approved v4 field keeps
  square LEDs and box-shaped light from the current node and last read, not a round glow.
  The full amendment is recorded under v4 below and in DESIGN.md; it replaces the earlier
  mode-dependent source tables. Local light must not flood the graph or obscure its controls.
- **R5: motion that reports work, settled by the maintainer on 2026-10-27.** The approved v4
  motion rules now replace the pending section in DESIGN.md: event-driven card, row, value,
  state and review transitions, graph folding, agent travel and merge settlement, all off under
  reduced motion. The v4 Motion decision below records the timing and marker behavior.
- **Shell zones accepted.** The top bar, left rail, sidebar, focus panel, graph stage,
  agents panel, bottom dock and status line give each feature a known home. The v4 Layout
  decision below and DESIGN.md now settle their dimensions and width rules. The maintainer updated
  AGENTS.md for focus panel tabs and the Files view. Approve keeps no keyboard shortcut, so the shell's keyboard map has no approve
  key, and Ctrl K offers no Approve command; the existing "Approve answers only a pointer click"
  decision applies. Nothing else waits on AGENTS.md.
- **Terminal placed now, built last.** Its home is the bottom dock. Implementation waits for an
  independently reviewed security plan: bind to 127.0.0.1, use a per-session token, check Origin
  and Host, and kill the terminal with the server. Agents never reach it. This reserves its place
  without adding shell access before its boundaries are reviewed.

### Approved v4 shell (maintainer, 2026-10-27)

- **v4 is the shell target (maintainer, 2026-10-27).** The seven approved images in
  `docs/sketches/v4/` define watch, focus, conversation, review, changes, settings and the agent
  marker. DESIGN.md adopts them inside Signal, retaining AGENTS.md's authority and the matte
  review header. Reason: later shell work needs one agreed visual target rather than accumulated
  additions; a sample in a sketch does not change a contract or make a feature available.
- **Layout: zones and docks (maintainer, 2026-10-27).** At 1920x1080, top bar 52px, left rail
  56px, sidebar 280px, focus panel 880px, graph at least 560px, agents panel 400px or rail 56px,
  bottom dock 280px high and status line 24px. Opening any focus tab folds the agents panel.
  By 1440px, sidebar and expanded agents panel overlay; below 1280px a width notice appears.
  With focus open, the sidebar overlays below 1832px; below 1552px the focus panel shrinks from
  880px, reserving 112px for both rails and 560px for the graph (608px of focus at 1280px).
  Explicit maximize may hide the graph and offers restoration. Ctrl K jumps and runs commands
  but has no Approve command; the keyboard map has no Approve key. Reason: files, diffs, review,
  conversation and settings need stable homes without losing the graph or making a merge fire
  from typing. The terminal is placed here but still built last.
- **Motion: R5 applied (maintainer, 2026-10-27).** Nothing moves without a cause. The enumerated
  card, step, value, pill, fold, review, highlight and merge transitions take 160 to 320ms with
  the existing easing. On each agent.focus the lettered marker glides along the edge route when
  one exists, carrying its light; approval fades both out. Working rings and the status lamp
  report ongoing work. Reduced motion disables every animation. Reason: visible event changes
  make work legible in use and on video without opening panels or stealing focus.
- **Surfaces: layered dark neutrals (maintainer, 2026-10-27).** Shell #09090a, panel #0d0d0f,
  page #101012, card #151518 to #111113, raised #1b1b1f and inset #09090b replace the dark
  surfaces. Divider is white at 6%, border at 10%, top highlight at 4.5%; cards have a soft
  shadow. Agent and slot cards carry a 2px agent-colored top line; the active tab uses its
  subject's agent color or neutral. Graph overlays frost at 12px blur; group labels are 11.5px,
  uppercase and tracked. Every color remains a theme token, accents and day colors unchanged.
  Reason: the approved layers distinguish work surfaces without adding a hue. DESIGN.md records
  the target token overrides, full contrast matrix and nearest passing proposals for failures;
  its frontmatter keeps the current theme until the shell front implements the target. Approved
  values are not silently adjusted. This supersedes the blanket ban on panel shadows and overlay blur.
- **R4 amended: the Signal LED field (maintainer, 2026-10-27).** Keep 7px square LEDs, three
  dithered lit levels, box-shaped light, a faint 28px resting grid and an expanding working ring.
  Only the current node (full) and last read (about 0.4) emit light; spreads are about 26px and
  72px at 100%, scaled with zoom. Title, legend, zoom controls and every overlay mask the field.
  Reason: preserve Signal's material and show exactly where an agent works without lighting an
  entire frame. The round-glow reading and former cone/selection light sources are superseded.
- **Settings as a focus tab (maintainer, 2026-10-27).** No repeated title inside. Left navigation
  and Saved line frame merge-mode cards and rules, budget input and threshold bar, steps stepper,
  size and cone inputs with units, protected-area rows with type/remove/add, and model-slot cards
  with model selectors; cross-review and key-status rows sit below the cards. Reason: make decisions
  and limits scannable in the same shell as the work. Values are illustrative: unsupported rows
  and choices, including a second provider-key row with no backing, are dropped. Fixed contract
  limits are not made editable by the sketch, and reserved features are not presented as working
  controls.

### Where Impeccable clashes with our rules

Ours wins in every case below.

- **Pure black.** "Never pure black" is a common rule of design skills; 4.5.2's files, as read, do
  not state it outright (its palette generator even offers pure black), but if it or its detector
  raises it, ours wins: the graph's ground is `#000000`, plain black until the dot field
  (AGENTS.md); panels use near-blacks.
- **Its core stance** ("go all out", "dream big and bold") against Signal's restraint: color is an
  agent or a state, panels stay plain within the approved tonal layers. Hence the forbidden commands.
- **"Dark or light is never a default; pick it from the use scene."** Ours: dark everywhere
  (AGENTS.md). The use scene is long supervision and a dark demo video; day mode is the gallery's.
- **The detector's "pulsing dot" and its craft floor's "one authored moment" of motion.** Ours: a
  working agent's status lamp pulses because it reports work in progress; the v4 R5 list also
  permits transitions tied to events. All stop under reduced motion.
- **The detector's "colored glow on a dark background".** Ours: the dot field's light and the rings
  are agent-colored light on black, because the light says which agent works where; it is dithered
  LEDs on its own canvas, not a glow on an element, brought forward without blocking a level.
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

- **Classes start folded (maintainer, 2026-10-27):** one row names the class and its method count; a click toggles it, and a new change adding or modifying a method opens it. Reads and cone membership project onto the folded row (nearest distance). Preset positions plus fold state determine all positions; only later rows and modules in that column slide for 240ms with cubic-bezier(0.2, 0.7, 0.2, 1), or jump under reduced motion. Incoming calls share a target trunk outside frames; endpoint taps alone traverse their own containing frame.

- **Graph cleanup evidence (2026-10-27):** comparison images and work reports stay outside the product repository; the start camera stays at 100%, frames settled header bounds, and treats the heading, notices and bottom overlays as viewport edges when choosing the nearest clear gutter.
- **Dot field brought forward (maintainer, 2026-10-27):** the dot field and working rings
  behind the graph move from "after level 1" to now, with the v4 R4 amendment above; it must never
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

Level 2 closed on Tue Oct 27 when `make demo-check` passed three runs in a row from a clean
start using the scripted fake model (anyone can rerun it), and `make e2e` passed over the
scripted two-agent replay. A real recorded run will be bundled for the static replay.

Level 3 wiring starts now. Contracts 1.4.0 delivery A was approved by the maintainer, 2026-10-27.

## 2026-10-27 — Approved shell frame and Chrome close-tab key

The maintainer's shell-frame brief supersedes the earlier flat-surface rules in DESIGN.md:
shell `#09090a`, panel `#0d0d0f`, page `#101012`, card gradient `#151518` to `#111113`,
raised `#1b1b1f`, inset `#09090b`, translucent dividers and borders, a top highlight and
soft card shadow. Graph overlays use the translucent panel token with 12 px backdrop blur.
Group labels use uppercase 11.5 px text with tracking. Agent accents stay unchanged.
The contrast matrix covers every requested text token on all seven surfaces and both card
ends at 4.5:1; no text token needed adjustment.

Follow-up: **Shell-frame docs front (DESIGN.md parity)** owns this supersession. When it
lands, update DESIGN.md to these approved surface values and M8's bounded-blur rules, then
remove the seven-token `approved` override in `frontend/src/theme/theme.test.ts` in the same
change, restoring direct parity with DESIGN.md. The parity test rejects overrides once the
documented value matches, so landing the docs update cannot silently retain a stale override.

At 1920 px the fixed zones are a 52 px header, 56 px view rail, optional 280 px sidebar,
880 px focus panel, graph of at least 560 px, 400 px agents panel or 56 px agents rail,
optional 280 px dock, and 24 px status line. At 1440 px and below, the sidebar and full
agents panel overlay. The focus panel yields width to preserve the graph's 560 px minimum.
Maximize is an explicit action; its Show graph button restores the same mounted graph.
The start-from-function task panel keeps its expanded exception, overlaying when necessary.

Focus tabs are person-owned reducer state: file, live diff and review retain their own
camera scope, six-tab cap, oldest inactive eviction and previous-active close behavior.
The rail's Watch button closes only the active tab, like Escape; other tabs remain open.
Closing the last tab returns to watch.
Events update tab contents and review state but never navigate. The dock and sidebar
contain honest placeholders until their owning fronts land. Replay provenance and connection
failures belong to the status line, with a retry that restarts only the transport session.

A headed Chromium test sends native XTest keys through Xvfb, rather than Playwright's CDP
keyboard dispatch (which bypasses browser accelerators). Native Ctrl+W closes the browser tab
without delivering W to the page; native Alt+W reaches it. Therefore Alt+W closes an Oversight
focus tab. Ctrl+B toggles the sidebar, Ctrl+backquote the dock, and Escape closes the active
tab. Shell shortcuts are ignored in text fields, task boxes and Monaco. Review's existing
keys remain local; Approve has no shortcut or command. While the shortcuts sheet is open,
all shell shortcuts pause except Escape, which dismisses only the sheet.

## Contracts 1.4.0 delivery A

1. **Guarded decisions and tree shape** — approved by the maintainer, 2026-10-27.
   Require revision/head guards, explicit live provenance and published agent bases. Reserve
   GET /tree with required nullable file hashes, a joint 2,000-entry cap and a future 8 MiB
   hashing budget; no traversal is enabled yet.
2. **Finish declarations** — approved by the maintainer, 2026-10-27.
   Optional files, tests_run and pytest ids preserve agent claims separately from observed facts.
3. **Evidence rules** — approved by the maintainer, 2026-10-27.
   Reserve cross_review, test_integrity, added_tests_fail_before, run_test_integrity,
   tests_reach_changes and within_zone; incomplete observations cannot authorize a merge.
4. **Fixed profiles** — approved by the maintainer, 2026-10-27.
   Keep the five-rule v1 set frozen. Review/integrity/probes have 6/7/8 rules;
   run/reach/zone each have 9 independently; v2 has all 11. Zone excludes run and reach.
   Profiles wait for their wired producers and explicit enablement.
5. **Server provenance** — approved by the maintainer, 2026-10-27.
   Requests cannot supply decision authority, starter identity or reviewer evidence.
6. **FIFO queue** — approved by the maintainer, 2026-10-27.
   Queue one standalone task or an exact plan revision; a plan reserves both slots together.
7. **Atomic A after level 2** — approved by the maintainer, 2026-10-27.
   Migrate contracts, generated outputs, routes, consumers, fixtures and guards together.
   Writer rejection is isolated per event; a terminal post-effect refusal retains the slot.
   Historical constructors use explicit replay mode, independently of fake-provider permission.
8. **Zones** — approved by the maintainer, 2026-10-27.
   Future assignments cover whole files or nodes with ranges and descendants.
9. **Lead standalone queueing** — approved by the maintainer, 2026-10-27.
   A lead may later queue individual tasks it owns; this grants no merge authority.
10. **Lead plan requests** — approved by the maintainer, 2026-10-27.
    A lead may request a plan; requesting grants no dispatch authority.
11. **Creation at 1.5.0** — approved by the maintainer, 2026-10-27.
    Keep SaveFile exactly 1.3 in A. Deliver required nullable base_hash only with safe creation,
    zones and inventory, including exclusive publication and verified existing parents.
12. **Bounded section 4.9 exception** — approved by the maintainer, 2026-10-27.
    Permit enum extensions and required guards in 1.4.0, then nullable base_hash in 1.5.0.
    Each version identifies one schema; this does not permit republishing changed 1.4.0 bytes.
13. **Personal plan authorization** — approved by the maintainer, 2026-10-27.
    Only the person queues and dispatches the exact reviewed plan revision.
14. **Plan agents belong to that authorization** — approved by the maintainer, 2026-10-27.
    Their AgentStatus.started_by is None even if a lead requested the plan. Plan.started_by
    preserves that requester; it does not grant the lead authority to revise these agents.
15. **API guard refusal code** — approved by the maintainer, 2026-10-27.
    Add forbidden (HTTP 403) within this same 1.4.0 delivery for requests whose Host or Origin
    is not on the allow list, supporting the planned DNS rebinding defense. The API guard is
    a separate front that supplies enforcement.
16. **Size refusal code** — approved by the maintainer, 2026-10-27 (M6).
    `too_large` (HTTP 413) rides in 1.4.0. Body and file limits are now enforced by the
    size-limits front, after API guard admission and before routing or side effects.

The tuple gate compares all changed array constraints with the byte-exact 1.3.0 baseline.
It discovers fields generically and checks generated TypeScript, including local references and
nullable unions. Four independent negative controls cover small max, min-only, historical
findings/evidence and a newly constrained existing field. Generator and TypeScript pins stay put.

## API guard — approved by the maintainer, 2026-10-27

- **G1:** Add `OVERSIGHT_VITE_PORT`. One shared policy permits only literal loopback
  authorities (`localhost`, `127.0.0.1`, `[::1]`) at the resolved backend and optional Vite ports.
- **G2:** The backend defaults V to unset. Empty V parses as unset; invalid ports, including
  V equal to P, fail startup. Ports are integers in 1-65535; omitted authority ports use
  protocol defaults.
- **G3:** Development launchers fall back to V=5710, resolve environment over `.env` once,
  and export P/V to both servers. Shots supplies distinct free ports; demo explicitly clears V.
- **G4:** With the terminal off, absent-Origin HTTP is allowed after Host validation, including
  writes, preserving CLI/eval/recording callers. Every supplied Origin must be one exact allowed
  `http://` origin. No CORS, forwarded-header trust, wildcard, suffix or DNS-based admission.
- **G5:** Refusals use the typed `forbidden` error with HTTP 403. WebSockets are denied before
  acceptance, by HTTP denial where supported and otherwise by closing the handshake.
- **G6:** Use the already approved `forbidden` code from contracts 1.4.0; this front changes no
  contracts. 1.5.0 remains reserved for safe creation, zones and inventory.

The guard precedes all routing in live and server replay, including static, unknown and reserved
routes. Pure ASGI preserves unbuffered SSE. A future terminal consumes this same policy and adds
same-authority checks for unsafe HTTP and every WebSocket, authentication and its stricter IPv4
listener requirement. See guard decisions G1–G3; their defaults and decline outcomes apply.

## Shared byte limits — 2026-10-27

`backend/src/oversight/limits.py` holds byte ceilings and bounded read helpers shared by the
independent backend modules, keeping import boundaries intact. HTTP bodies and event lines have
separate 1 MiB limits; regular files use 256 KiB, while agent ranged reads allow up to 64 MiB.
Event line limits exclude the trailing newline and apply to both import and export. Ledger
writes are compact and refuse growth beyond their read cap before replacing the previous file.

## Monaco's vendored DOMPurify — 2026-10-27

Pin Monaco's DOMPurify dependency to 3.4.17 and patch monaco-editor@0.57.0 with the official
3.4.17 ES build from the pnpm store, preserving its license and default export and omitting only
the sourcemap reference. Its vendored 3.4.15 build matches the official 3.4.15 ES build apart from
the absent sourcemap reference. Monaco imports that vendored file directly from domSanitize.js,
so a dependency override alone does not fix the sanitizer the app runs. Monaco 0.57.0 is the
latest release and no Monaco release includes the fix yet. Drop the patch once Monaco ships
DOMPurify >= 3.4.17; an offline unit test in make check pins the vendored version so an update
cannot silently lose the patch.

DOMPurify 3.4.17 fixes GHSA-6688-9rhm-gjv2, GHSA-c6gr-qvv4-5v3f,
GHSA-cg2g-xh64-v4q7 (critical), GHSA-mrv2-3pqr-j5jr (high), and GHSA-p98j-92pf-mc4p.
Published on 2026-10-12, it satisfies the 14-day release-age rule. The app never enables IN_PLACE,
SAFE_FOR_TEMPLATES, or the XHTML parser, so this is defense in depth. No other dependency or
version changes. The patch is kept as the official build rather than reformatted or minimized,
even though replacing it produces a patch over 500 lines.

DOMPurify is dual-licensed (`MPL-2.0 OR Apache-2.0`); this project takes it under Apache-2.0.

Verification: the patched `esm/vs/base/browser/dompurify/dompurify.js` equals `dompurify@3.4.17`'s `dist/purify.es.mjs` except the sourcemap line.
Monaco's `min/` and `dev/` bundles still carry 3.4.15; the app does not load them.

## Dependency licenses

- **Named typing-extensions exception (decided by the maintainer, 2026-10-27):**
  typing-extensions uses PSF-2.0, a permissive license, and is a transitive runtime
  dependency of pydantic, pydantic-core, FastAPI, starlette, anyio, httpx2, openai,
  opentelemetry-api and typing-inspection, so it cannot be dropped. The license
  check permits only the exact pair `typing-extensions` and `PSF-2.0`; the
  exception covers that package only. Every other license outside the AGENTS.md
  list is rejected, with OFL-1.1 still restricted to the two Atkinson font
  packages. THIRD_PARTY_NOTICES.md retains the PSF-2.0 declaration.

## 2026-10-27 — M8: bounded backdrop blur

M8 narrows the shell-frame exception to DESIGN.md's Flat Panel Rule: only the graph legend,
zoom controls and segmented controls may use the fixed 12 px `overlay-blur` token. Segmented
controls are reserved for a later front; none are added here. Other overlays and popovers use
the 96% `overlay-fill` theme token with `top-highlight`, without backdrop blur. This covers the
watch heading, impact cone, narrow-window notice and shortcuts sheet. Blur radii never animate.
The earlier shell zones, colors, tabs and keyboard decisions remain in force.

Compositor evidence is reproducible with `node frontend/shots/compositor.ts --label before`
on the pre-M8 tree and `--label after` on M8. Both use the Vite dev shell fixture at 1920×1080,
review's camera, a deterministic 180-frame pan and a controlled JS field clock; three samples
per version. The runner verifies identical camera paths, layout positions and field draw counts.
Raw Chromium traces and JSON reports stay in `.shots/`. Its metric is the union of top-level
task durations on renderer Compositor and VizCompositorThread, summed across threads, in ms;
it is compositor task occupancy, not GPU execution time or a frame-latency measurement.

Measured results (Chromium 156.0.8078.4, ANGLE SwiftShader software renderer):

| Version | Commit | Samples | Median compositor occupancy (ms) |
| --- | --- | --- | --- |
| before | `16842be` | 3 | 2189.663 |
| after | `b1c2a82` | 3 | 1816.517 |

Delta (after − before): -373.146 ms (-17.041%). The sample totals were
2613.844, 2083.882, 2189.663 ms before and 1199.853, 2270.919, 1816.517 ms after.
Each sample has 180 camera frames and 177 field draws, with identical motion across all six.
`frontend/shots/compositor-evidence.json` preserves the totals, commit ids, renderer and motion
hashes; its test checks the medians, delta and sample counts against this entry. During the
review fix, the totals were recalculated from all six raw traces and matched these reports.
The measured median improved, but three samples with this dispersion and a software renderer
do not establish a hardware-GPU speedup or a frame-latency improvement.

## Cross-review core and provisional reviewer budget

Cross-review is opt-in (`OVERSIGHT_CROSS_REVIEW=false`). Its core and adapter do not schedule
reviews or publish events yet. A reviewer uses the opposite slot's model, a fresh client and a
separate message history. Fake reviews require an entirely fake configuration and use scripts
named for the reviewer, independently of the author scripts.

The reviewer uses `REVIEW_MAX_OUTPUT_TOKENS = 12_288` with explicit `reasoning_effort="low"`
on Meta and Baseten. This provisional budget leaves room for twenty findings of 1,000 characters
and reasoning; 32,768 has no documented provider validation. Paid acceptance must measure provider
acceptance and long-output throughput at the actual budget against the planned 90-second deadline.
No paid probe was run for this change. Length, empty or invalid output stays diagnosed unknown,
without a recovery call or a silent budget reduction. `run.started.limits.max_output_tokens`
continues to describe the author's 4,096-token budget; the review budget has no event field in the
frozen 1.4.0 contracts.

## 2026-10-27 — Union merges for the decision log

Use Git's built-in union merge driver for `DECISIONS.md`, declared as
`DECISIONS.md merge=union` in `.gitattributes`. Parallel fronts append decisions at the same
end of the file; union keeps both entries without the repeated merge conflicts. Offline tooling
tests exercise both `git merge` and `git merge-tree --write-tree` in temporary repositories.

Union also keeps both sides when two branches edit the same existing line, reporting no
conflict even when the decisions disagree. Reviewers must check edits to old entries for
duplicates or contradictions; a clean merge does not establish agreement.
## Cross-review authority and maintainer decisions (Oct 27, 22:17 ART)

The maintainer accepted package 2 decisions 6a–6c on October 27 at 22:17 ART (UTC−3):

- 6a: the cross-review switch stays false by default; the demo enables it explicitly.
- 6b: parity accounting records an unanswered Meta review attempt as unknown and closes the
  Meta spending brake. Recovery requires stopping the server, checking Meta's total and running
  `ledger settle`; settlement is refused while the server runs. Restart creates a clean workspace
  and a new run/log, and resumes no earlier change.
- 6c: approving A does not invalidate B's review of its captured revision, head, base and tree.
  Request changes keeps that base; Approve must test the combination with current main.

PR2 implements authority and the single automatic revision round: admitted human decisions or
review saves permanently disarm automation for that change; automatic requests reserve only
ready changes, retain cross-review evidence through pending delivery, and consume the round when
its resolution is created. It also owns the shared closed Needs you vocabulary, template and golden
fixture, which package 1 must adopt. It adds no model calls or automatic review scheduling.
PR1 already supplies the default-off switch. PR3a carries runtime registration, cancellation and
lifecycle wiring, including explicit demo activation; PR3b carries call scope, accounting, the
Meta brake and distinct review-ledger failure handling. PR4 carries replay/UI projection and
recognition of the shared fixture. Contracts remain frozen at 1.4.0.

## Cross-review runtime lifecycle (PR3a)

Confirmed, uninterrupted agent proposal facts notify an internal ReviewPort with the captured
candidate and explicit authorship. LiveRun waits for run_ended and a free, ready author before
reviewing. Human saves never arm reviews; halted authors receive Needs you without new budget.
HTTP and automatic Request changes share synchronous admission and task registration. Human
admission cancels tracked reviews; shutdown closes admission and drains reviews before runs.

The reviewer callable is injected. PR3a tests drive the PR1 adapter with scripted clients;
production call transport remains unconfigured until PR3b supplies its scope and accounting.
Enabling the switch without that transport produces reviewer_missing without a model call.
The default-off path adds no events or tasks and preserves the existing level 2 replay bytes.
Contracts stay at 1.4.0; reducer and UI projection remain PR4.

## Cross-review call scope and accounting (PR3b)

Production reviews use the agents facade's run_review_call in a fresh context. The helper owns
the ready + 90-second timeout, retry deadline and review callback, restoring the scope on exit.
Normal responses and empty choices record once; unanswered requests record unknown usage.
Cancellation before a request or during retry backoff adds no tick. Durable accounting drains
even after human invalidation, using the reviewer's provider rather than the author's provider.
Baseten review usage remains unpriced and never consumes Meta's credits, as for Baseten authors.

Authors and reviews share the durable ledger writer but have separate failure handlers. A lost
review write closes the Meta brake and publishes a drained, non-fatal Needs you ledger error with
the captured target and reviewer identity. It never marks or halts the author, and never publishes
the failed tick as persisted. Concurrent cancellation propagates after the error is recorded.
The existing 12,288-token provisional review budget and frozen 1.4.0 contracts remain unchanged;
reducer/UI projection belongs to PR4, and frontend review-cost separation remains a follow-up.
## 2026-10-28 — Incremental graph presentation

Guard Cytoscape data, class and interaction-state writes: even equal writes trigger expensive
compound restyles and SVG hashing. Keep fold scratch outside the style setter, retain selection
and routing classes, share layers across background properties and encode embedded fonts once
per family; keep the same SVG bytes, geometry, tokens and motion design.

Give Cytoscape one short `blob:` URL per distinct layer image to avoid hashing font-sized data
URIs on every restyle. Revoke the URLs when their stylesheet is replaced or the graph is destroyed.
A future Content Security Policy must allow `blob:` in `img-src`.

While the camera or elements animate, request a render from a microtask after each animation
step: Cytoscape 3.34 clears the step's redraw flag (cytoscape.js#3544). Install once per core and
guard the private animation pool with a test; remove the helper with Cytoscape 3.34.5 or later.

## Contracts 1.5.0 — approved by the maintainer, 2026-10-28

Reserve the beyond-level-2 shapes without producing new evidence or enabling behavior:
A1 facts summary; A2 per-node behavior diff; A3 measured callers alongside the static cone;
A4 review cards and human answers; A5 usage by change, role/model and revision round;
A6 task examples; A7 confirmed pins; A8 node examples with provenance; A9 neutral shape facts.
B1 adds protected_paths and its fixed profile; B2 adds two-slot dependencies; B3 distinguishes
temporal weakening kinds; B4 carries decision answers; B5 reserves examples-only starts;
B6 adds archived usage and revision rounds; B7 reserves dictation request/response roots and
POST /dictation, answering 501 not_implemented in live and replay before reading the body.
Every attachment has a null or empty default so old payloads and recordings remain valid.

The seven coordinator amendments override the original draft:
1. Summary is a list of one to three single lines, each at most 240 characters, without padding.
2. Behavior rows retain differing or unstable inputs only; equal results count in compared_count.
3. At most eight cards/answers, one per unrequested choice, with node_id and an optional example.
4. Usage roles match ticks: agent, review, plan and lead; accepted-change cost stays derived.
5. ExamplePin, TaskExample and NodeExample live in examples.py; StartAgent accepts at most five.
6. safe-merge-paths-v1 adds only protected_paths to probes; all existing profiles stay frozen.
7. The delivery manifest is a frontend-only typed file; it requires no contract or event change.

The active backend profile remains safe-merge-probes-v1. No protected-path evaluator, producers,
UI, Revert or delivery manifest is implemented here. Cost per node and exception type are left
out. These additions identify schema 1.5.0; safe creation, nullable SaveFile.base_hash, zones and
inventory move to 1.6.0. Delivery A decisions 11–12 remain unchanged as the approval history.
The tuple check now compares against the byte-exact 1.4.0 baseline, retaining the 1.3.0 baseline
and negative controls. Baseline snapshots count toward the commit line limit; an indivisible
snapshot requires the existing Commit-Exception trailer. Pydantic/JSON Schema enforce list bounds
while TypeScript keeps ordinary arrays; the existing explicit two-slot tuples remain tuples.
## 2026-10-28 — Release-age exception for certifi

certifi 2026.7.22 bundled the emSign root CAs whose intermediate keys were compromised
(GHSA-6q48-6c7w-9gw2, critical); 2026.10.18 removes them. That release is ten days old, short of
the 14-day rule, so `pyproject.toml` exempts certifi alone with a cutoff that admits 2026.10.18 and
nothing later. certifi is transitive (through the dev-only httpx 0.28.1 and httpcore 1.0.9), its
license is unchanged, and every other package keeps the 14-day rule. Drop the exception after
Nov 1, when the release passes 14 days on its own.

Audit blind spot: `make audit` exports only the Python runtime closure (`--no-dev`), so dev-only
certifi is outside its pip-audit coverage. Dependabot detected GHSA-6q48-6c7w-9gw2; the exception
affects development and tests, not runtime TLS. A dev-inclusive audit remains follow-up work.
## 2026-10-28 — One inventory beyond level 2

Make [docs/additions.md](docs/additions.md) the single inventory: decided queue with main/branch
state and contract status, unranked delivery/graph/bench work, undecided candidates, Next, No and
numbered descriptions. README mirrors Next and keeps its zoom sketch. Start the first available
unfinished queue item when capacity opens; the old date-based package schedule no longer chooses
fronts. This removes competing priorities while retaining completed work as evidence. The approved
proposal stays unchanged, and the level 2 demo remains the fallback until its replacement passes
three clean starts.

## 2026-10-28 — Two audiences, one supervision mechanism

The supervising developer and judges need visible control; a person running many coding agents
also needs throughput, with attention spent on consequential decisions. These use the same facts,
rules and exception cards. Proposed PRODUCT Users wording: “For developers supervising coding
agents, including people who want several agents working while they handle the decisions that
need them.” Proposed Positioning wording: “Oversight lets agents keep working while you decide
which outcomes are acceptable, using execution evidence and explicit rules.” These are proposals
for the maintainer to review, not edits or approval to merge PRODUCT wording.

## 2026-10-28 — The decided queue replaces the package calendar

Use the 18 positions in [the queue](docs/additions.md#decided-queue): observed facts first, behavior
comparison and the harness next, then measured callers, overlap and stronger test evidence before
rules/card work and the later planning/example features. Delivery, graph polish and the task bench
remain unranked commitments. Keep package numbers only as references. The lead agent moves to
Next: its former Sunday prerequisite included packages 1–6, and the run rule is now item 17.
This gives scarce review and build capacity one order without scheduling already merged work again.

## 2026-10-28 — One optional contract proposal, with explicit open questions

Request optional representations for the generated summary, per-node behavior differences and
unstable outcomes, measured-cone comparisons and unmatched calls, card questions and Yes/No/Unsure
answers, change/role costs, task examples, confirmed pins with confirmer/task provenance, node
examples with origin, and shape findings. A request is not a reservation or approval. Preserve the
existing optional-field and unbuilt-endpoint-501 policy; do not alter contracts on a feature front.
Harness improvements and observed overlap need no contract addition.

The report contradiction check fits existing events and review derivation; do not invent an
integrity kind for prose. A fixed protected-path server veto fits the existing boundary, but a
named deterministic policy rule needs an addition to the closed `RuleId` and profile validation
(`contracts/src/oversight_contracts/common.py:44`). This open question is superseded by the dated
contract-proposal approval below. The code also shows that dependent starts and a single-agent
fallback exceed the two-agent running-plan validator
(`contracts/src/oversight_contracts/plans.py:60`); propose that extension before wiring it. These
questions prevent optional properties from hiding changes to the meaning of a contract.

## 2026-10-28 — Bench criteria and a shared US$20 ceiling

All paid bench comparisons together must fit US$20 of the US$150 budget. Pilot one task per arm,
project total spend, reduce repetitions before arms if over cap, and run the higher-reasoning arm
only if the remaining comparisons fit. Criteria precede execution; numbers about Oversight must
come from these runs, not from papers or a successful development example.

For behavior/intent, freeze 10 changes with an unrequested difference and 10 with only requested
differences, labeled by the maintainer before detector output. Let none of the first group through;
ask needlessly about at most two of the second. Compare tests alone, Muse Spark reviewing the
diff, and behavior comparison plus the advisory judgment, on identical patches. Separate detection
from classification, retain unprobed cases in denominators, and exclude development examples.
For the harness, compare current tools with the pack on the existing eight tasks, three repetitions
per task/arm, randomized arm order and equal model/reasoning/limits. Declare solved tasks primary;
also measure turns, tokens, dollars and wall time. A higher-reasoning current-tools arm is optional.
Compare graph zoning against a single agent doing both tasks and a naive split on joint correctness,
wall time, tokens and cost. Compare one-hop context with no slice on steps, tokens and tasks solved.
This keeps correctness and spend visible alongside any speed claim. No paid execution is part of
the documentation front.

## 2026-10-28 — Next and No are separate scope decisions

Keep the maintainer's ordered Next list in additions and README, including the deferred lead,
evaluation extensions, phone answers, area text rules, focused tests/coverage and measured context
reductions. Existing external hidden checks, seeded bench defects and a single automatic revision
do not implement their broader product counterparts. Keep rejected ideas in No with an individual
reason: no inferred autonomy, opaque learned risk, agent chat/locks, unbounded graph context, voice
approval, mutation pipeline, logprob map, city/3D view, multiple-approach prompts, Hypothesis,
trained verifier or tree search. These cuts preserve explicit authority, bounded cost and the
dependency policy; they are not an unprioritized second backlog.

## 2026-10-28 — Lead with the supervision claim and acknowledge prior work

Proposed README opening for maintainer approval: “Coding agents can produce changes faster than
a person can inspect them. Oversight helps you supervise their results through measured examples,
rules and the decisions that need you.” The IDE is the working surface; control and throughput
are the proposition. Leave the existing opening untouched until the maintainer approves its copy.
Never claim priority or a benefit not measured in our own bench.

Behavior comparison has prior work in [Testora (ICSE 2026)](https://arxiv.org/html/2503.18597v3)
and [DiffTestGen (2026 preprint)](https://arxiv.org/html/2607.16024v1); both investigate behavioral
differences across versions. [Just-in-Time Catching Test Generation at Meta (FSE 2026 Industry)](https://arxiv.org/html/2601.22832)
uses change-directed catching tests. [CONTRA (2026 preprint)](https://arxiv.org/html/2610.01769v1)
checks whether clarification questions distinguish behavior by executing programs. Cite these
within those scopes, not as evidence for Oversight's accuracy or speed. This separates the product's
measured contribution from techniques that already exist.

## 2026-10-28 — A result-led video, with a proven fallback

The maintainer chooses the script on Mon Nov 2. Open on a real agent change with a plausible diff
and passing tests, invite an approval decision, reveal an unrequested before/after result and its
measured cone, then show scale with counts from a real run. Keep editor, file view and editable
review side out of the video: the evidence and decision are the story. The level 2 script remains
the fallback until a replacement runs three times from clean starts with one command and no manual
fixes. Keep the Tue Nov 3 20:00 ART robustness/takes freeze and Wed Nov 4 20:00 ART submission target.

## 2026-10-28 — Static delivery asks the visitor to decide

After behavior diff reaches main, prioritize package 9's static three-change challenge. Visitors
choose Approve or Reject, then see findings and a score. Reuse backend-free replay with a companion
challenge manifest and local answers; no new server contract is necessary. Main's current two
recorded proposals are not a three-case challenge. Label planted cases explicitly and preserve
real-recording provenance only for authentic, unmodified recordings. The point is a review decision
the visitor can experience without credentials or a backend, without disguising synthetic evidence.

## 2026-10-28 — Evidence and claims stay attributable

Read the relevant technical evidence when a front starts, and check its premises against the
current main before choosing an implementation. Cite published work only within the verified
claim's scope, in fresh wording with a direct primary-source link. Keep external figures attached
to their original experiment; Oversight numbers require our own bench. Planning material is not
product code or a public citation. This prevents outdated premises and borrowed results from
becoming claims about this build.

## 2026-10-28 — Behavior diff core precedes publication

Coordinator ruling: implement a private behavior-diff core behind a setting before its event/UI
publication. Do not publish results until the maintainer approves the contract field, including
unsupported and unstable outcomes. This lets implementation proceed while the schema remains
under the maintainer's authority, without silently extending the wire format.

## 2026-10-28 — Behavioral probes require session network isolation

Coordinator ruling: behavior diff and measured-cone runs must use the session's required isolation
boundary on both versions, never an open-network fallback. C3 is on main in `19443e8`
(`backend/src/oversight/workspace/capability.py:116`); loss or absence of that capability means
unavailable evidence. Reuse the test-run caps and guarded asynchronous execution. Network isolation
does not contain filesystem effects, so unsupported side effects remain unprobed. This avoids
turning an optional evidence producer into a less restricted execution path.

## 2026-10-28 — Exploratory spike is not implementation code

Coordinator ruling: write the behavior probe afresh within this repository. The exploratory spike
may inform a technique, but supplies no copied code. The implementation must satisfy our typed
comparison, repeated-input stability, named binding, process freshness, caps and isolation rules.
This keeps hackathon provenance intact and prevents prototype shortcuts becoming product behavior.

## 2026-10-28 — One switch for the harness comparison

Coordinator ruling: place the full harness pack behind one setting, preserving the current tools
as the comparison arm. Both arms must run with the same model, reasoning and limits. The switch
covers syntax protection, search summaries, failure preservation, the finish reminder and prompt
changes; no contract addition is needed. A single switch makes the comparison attributable to the
pack instead of an accidental mixture of provider and tool changes.

## 2026-10-28 — Bench repetitions extend the ledger instead of resetting it

Coordinator ruling: add arm and repetition to durable bench attempt identity alongside task and
profile, preserving charges, unknowns and outstanding reservations. Main's `tools/eval/ledger.py:112`
still identifies completion by task/profile; `tools/bench-arms` owns the extension. Never erase or
reset the ledger or create another output directory as a repetition mechanism. A retry with lost
accounting would evade the shared cap and make the measurements irreproducible.

## 2026-10-28 — Remove completed cross-review and small packages from work

Coordinator ruling: queue item 7 is implemented through `7da6fa9`, after `95e4723`, `9b81d0b` and
`13f955c`; do not open another automatic-cross-review front. Package 11 is on main in `ee92691`
(revision callers/siblings), and package 12 in `c1c19c6` (start from a function); remove them from
remaining package work. Turning cross-review on with the other model and using its result in the
merge-alone evaluator belong to item 8. Retain their rows and code evidence in additions for traceability. This
corrects the older inventory without confusing unavailable manual routes with missing automation.

## 2026-10-28 — Implement the remainder supported by current code

The code audit narrows item 6 to distinguishing assertion failures from import/setup errors:
`b7122ef` already supplies base/head probes, and `19443e8` requires isolation. Conservative unknown
classification fits existing facts. The later contract-proposal ruling below declines an
exception-type field and keeps this correction within the current contract.
Item 1 reuses report contradictions and `ae9822d`'s facts row rather than reimplementing package 3.
Item 5 reuses shared pending-node marks but still needs change-lifetime observed reads. Item 17
starts with the wire's assertion-loosening case: the pure core also handles tests that disappear
or skip, which cannot be described as observed passes. Source-only node examples cannot be
labeled observed execution; the later lens ruling below drops node-cost allocation entirely.
These choices preserve the decided user outcomes and queue while declining stronger claims than the code supports;
contract, look, spending or acceptance-criterion changes still go to the maintainer first.

## 2026-10-28 — Approved additions to the Oct 28 contract proposal

The maintainer approved including `protected_paths` as a new `RuleId` in a new policy profile
version for item 8. The identifier set is closed and profiles validate against it; a server-only
veto would omit the named policy reason. This supersedes the pending protected-path question above.
The report check in item 1 uses existing shapes, including contradictions between a passing-tests
claim and the last failing run. Keep the optional generated summary for historical use.

Also include optional subtask dependencies with a relaxed plan validator for item 11; a run-rule
finding kind distinguishing removed, skipped and deleted checks for item 17; Yes/No/Unsure answers
on approve for item 9; an examples-only start for item 12; cost and rounds stored on each change
for item 10; and a separate dictation endpoint answering 501 until implemented for item 18.
These cover the decided workflows while preserving compatibility and explicit unbuilt responses.
The cost per node field in usage events and the exception type field in test results were declined:
node costs lack direct attribution, and conservative base-error handling needs no new field.
This approves proposal scope, not edits to the approved proposal or current contracts on this front.

## 2026-10-28 — Graph lenses use directly attributable evidence

The Cost per node lens is dropped. Spending is measured per call and per agent; assigning it to
nodes would make an estimate look like a measurement. Keep only Tests, Hubs and Pinned examples
in item 13's lens control. The related usage-event field is also excluded from the proposal.
This supersedes the earlier allowance for labeled node-cost estimates.

## 2026-10-28 — Node examples begin with labeled test source

Deliver item 14 first as the test source at the function's call site, explicitly labeled source.
Real argument values depend on item 4's recorder and appear only after that evidence exists.
Keep recorded, proposed and confirmed examples distinguishable. This gives the person a useful
first card without turning a static call edge or code excerpt into claimed execution evidence.

## 2026-10-28 — Base import and setup errors are unknown evidence

Move item 6's remaining base-error correction ahead of merge-alone activation, with no contract
change. An import or setup error on base means unknown, never evidence that the test failed before
the change. The merge-alone evaluator must exclude it as supporting evidence. Existing diagnostics
can carry this distinction, so the correction neither waits for an exception-type field nor lets
unexecuted assertions justify an automatic merge.

## 2026-10-28 — Baseten comparisons require an explicit tariff

Agent B's Baseten spend stays unknown until its price per million tokens is configured in `.env`.
Require that explicit tariff before paid bench comparisons: the shared US$20 ceiling cannot be
enforced with an unpriced arm. Unknown cost is never zero and never debits the Meta credit ledger.
This makes the budget rule apply to both providers without inventing a price.

## 2026-10-28 — Agents receive graph context as server-built text

Item 16 passes its bounded graph context only as text assembled by the server, never a graph tool
for an agent. AGENTS.md reserves the graph as the person's supervision surface; Request changes
already supplies related source context as text. Reuse that precedent for initial tasks so the
agent gets a bounded hint without acquiring a new graph interface or unbounded traversal.

## 2026-10-28 — D1: The freeze must demonstrate three proofs

Judge every front by the [three proofs](docs/additions.md#three-proofs) it serves. Package 9's
static Would you approve it? challenge is the submission link and opens the video. It must work
at phone width, with a phone-width shot shown to the maintainer before merge. The second proof
is one person supervising many agents for a few hours on a real permissively licensed open-source
repository, credited in the video. The coordinator proposes the repository and tasks first and
reports budget pace before starting. End with counts of rules merges, questions and differences
tests missed, plus cost per accepted change. Paid calls for this run stay within US$25 of the
Meta budget, separate from the bench's US$20.

The third proof uses that same run: the share of green patches whose behavior diff finds an
unrequested difference. Decide before measuring that a low share or small sample means a scale
story without a percentage; never repeat the run to improve the result. At Sat Oct 31, 23:00 ART,
items 1, 2, 4 and 9, the behavior-diff bench and proof 1 must all be on main. Otherwise cut from
item 12 down and report the cuts to the maintainer. These proofs focus the freeze on something a
judge can try and measured supervision, while fixing the reporting rule before results are known.

## 2026-10-28 — D2: A thin lead starts agents through the public commands

Move the [lead agent](docs/additions.md#lead-agent) out of Next and place it immediately after
item 11, building it once items 8 and 11 reach main. The server keeps zone computation and the
task queue. The lead writes only the plan and subtasks and starts agents through public commands,
using the reserved lead routes and `started_by`. It never merges or reviews and has no information
hidden from the person. This lets proof 2 start many agents without making the person start each
one, while preserving the existing authority and evidence boundaries.

## 2026-10-28 — D3: Saved flows fill task text before an agent starts

Place [saved agent flows](docs/additions.md#saved-agent-flows) after the lead. Extend package 12's
shipped Start an agent here with a short list of files in the repository: each names task text,
a target (selected node, selected change or files changed since the last commit) and expected
checks. The person selects a flow, reads its filled task text, may edit it and starts it. Start
with tests for the selected function or changed files, a counterexample to the selected change,
and an explanation of that change from its facts. The counterexample flow is a light form of the
red agent; the broader red agent stays in Next.

Results use the same card, rules and evidence as any change. Task-text filling needs no contract
change; a distinct role or tool limits go to the maintainer first. Show the maintainer a shot of
the picker before merge. This makes repeated tasks easier to start without introducing a separate
review path or silently extending agent permissions.

## 2026-10-28 — D4: Keep item numbers while changing the work order

Use [the queue](docs/additions.md#decided-queue) in this order: items 1–11, the lead agent, saved
flows, 12, 13, 17 and 18; then 14, 15 and 16 only if time remains. The lead and flows are queued,
unnumbered rows after item 11, with their prerequisites. Existing numbers remain stable because
the maintainer uses them to refer to work. This supersedes the earlier queue order without
breaking those references and puts the multi-agent proof ahead of the optional later work.

## 2026-10-28 — D5: Non-Python editor highlighting has shipped

Record [editor language highlighting](docs/additions.md#shipped-editor-language-highlighting) as
shipped on main in `e1e5599` and `b238a6c`. YAML, Markdown, JSON, TOML, shell and INI select their
language by extension in the file view and live diff, without language-service workers
(`frontend/src/editor/language.ts`, `FileView.tsx:193`, `LiveDiff.tsx:266`). Files that create no
graph nodes show a discreet No impact analysis header label. This makes other repository files
readable without suggesting that Oversight analyzes their impact or adding language services.

## 2026-10-28 — D6: Give Spark Open a constrained decision on behavior differences

Hosted Muse Spark writes code and runs the lead. The maintainer's same-day follow-up fixes both
authors, A and B, on Muse Spark; Muse Spark Open is the cross-reviewer and deciding model.
Cross-review stays as it is. For [item 9](docs/additions.md#9-a-change-card-with-three-states),
Spark Open makes one constrained choice per behavior difference: asked for, not asked for or
cannot tell, through a `json_schema` enum or grammar. Log probabilities over these options give
a decision probability; below a threshold, or on cannot tell, the change waits. This can only
add a reason to wait, never approve.

Probe first: `tools/probe_api.py` checks log probabilities and structured output on both endpoints,
with reasoning off for this call. About ten development examples then test whether the probability
separates correct and incorrect answers. Build probabilities only if both conditions hold;
otherwise keep the constrained choice, with cannot tell as its only reason to wait, and report
the findings to the maintainer. This finite-decision probability differs from reading likelihood
over code tokens as defect evidence; retain that rejection with the distinction beside it in
[No](docs/additions.md#no).

Add a [behavior-diff bench arm](docs/additions.md#delivery-graph-polish-and-bench) on the same 20
frozen patches, replacing Muse Spark's advisory call with Spark Open's decision. Report calibration
alongside detection and classification: whether an above-threshold probability means the answer
was right, if probabilities pass the probe-first condition. Set the threshold from development
examples, never the 20 patches. Spend stays inside the bench's US$20 once the Baseten tariff is
configured. This gives the open model a distinct, bounded role and tests the probability before
relying on it. PRODUCT and video wording will be proposed to the maintainer later.

## 2026-10-28 — D7: Ask before repeating an unchanged failure

The maintainer sets a guard for automatic revision rounds, whether from cross-review or rules
follow-up. Before starting a round, the server compares the failure with the last one: the same
test and the same assertion. If the agent's edits since then touched nothing that reaches that
test, it starts no further round and the card asks the person.

Use the graph's call edges: a changed node reaches the test when the test can call it, directly
or through other nodes. Also use the test's executed nodes when recorded: touching one counts as
reaching that test. The check is deterministic and makes no model call. If neither source can
establish reachability or its absence, the card asks; it never starts another round on a guess.
Keep this in Undecided candidates, outside the decided queue. Repeating an unchanged failure
spends credits and the person's attention for nothing.

## 2026-10-28 — D8: Attribute video numbers to the team's build process

The maintainer allows the video's impact beat to use numbers from building Oversight with a
hand-run version of the workflow it automates. Examples include rounds per front, full suites
run per round, writer slots held, and collisions between fronts found only at landing or on the
merged tree, such as a semantic test conflict found by the merged-tree `make check`.

Label these as the team's build process, never as Oversight measurements. The note containing
the numbers stays outside the repository. This supplies attributable process evidence without
presenting development work as measured product behavior.

## 2026-10-28 — D9: Behavior probes for methods wait for level 3

The maintainer places method support for behavior diff at level 3, built only if that level is
reached. Today the target filter accepts module-level functions only
(`backend/src/oversight/changes/_behavior.py:79`); methods are explicitly not probed
(`backend/src/oversight/changes/_behavior.py:76-77`). Keep the extension in Undecided candidates,
outside the decided queue.

Until then, measure the third proof, the share of green patches with an unrequested difference,
on tasks that change functions only, and say so in the write-up. This keeps the measurement within
what the current probe can compare instead of implying evidence about methods.

## 2026-10-28 — D10: The D6 probe did not provide decision probabilities

The paid probe required by D6 ran on purpose on 2026-10-28 at 16:45 ART, using
`tools/probe_api.py`, with four calls per endpoint. Reasoning could not be switched off on either
endpoint. Muse Spark on Meta still reported reasoning tokens with `reasoning_effort` set to
`minimal`. Muse Spark Open on Baseten still reported them with `none` and with the chat template's
thinking switch off.

Both endpoints accepted a choice constrained by a strict JSON Schema enum: each returned HTTP
200 and one of the three options. Neither supplied log probabilities over those options. Meta
returned HTTP 400 because `logprobs` is not supported together with reasoning. Baseten returned
an answer without log-probability content.

Under D6's probe-first rule, the decision probability is not built because the required log
probabilities were unavailable. What replaces the threshold remains a pending maintainer decision.
## 2026-10-28 — A decisions inbox is the form of Needs you

The maintainer decided that the decisions inbox is the form of item 9's Needs you state, not a
new item or number. One place lists every decision waiting for the person across all changes
and agents: the card's Yes/No/Unsure questions; checks that could not run; a failure or conflict
while following main (item 8); a risky-area change (item 8); the lead's plan when it needs
approval; and proposed examples before code (item 12).

Each entry shows its change or agent, why it waits, its smallest example and its age, oldest
blocking first. A header count shows how many decisions are pending. The person answers from
the inbox, and the answer goes to its originating change. No is Request changes to its author,
as today. If the person picks another agent, start a new task for that agent with the answer as
its text. Unsure stays in the inbox. Questions come only from Oversight, never from the agent.

Derive the inbox as a pure function over existing events in the frontend reducer, like the
overlap in item 5, so it survives restarts and appears in replay, with no field beyond item 9's.
One contract question remains open: an answer routed to another agent may need an optional link
to come back as answered after a restart or in replay. Today a start has no reference back to a
card, and answers persist only in the change's resolution. The maintainer has not answered this.

Proof 2 ends on the inbox: this many agents for this many hours, and these were the only decisions
the person had to make. Build the inbox together with item 9, keeping item numbers, anchors and
the decided queue's order. It changes the look, so the maintainer sees a shot before merge.
This gives the person one place to answer and makes the run's need for human decisions visible.

## 2026-10-28 — Contracts 1.6.0 — approved by the maintainer

Slot now admits A through F as identifiers only. Existing two-subtask plans keep their explicit
pair tuples, dependency indices 0/1 and assignment validators; widening slots does not enlarge a
plan. OVERSIGHT_MAX_AGENTS, default 4 and maximum 6, is a backend setting outside contracts. The
backend offers the first N letters from A (integer 1–6, default 4), in letter order. A fake
slot other than A needs its own script to be offered; a fake A without a script fails startup.
Admission reserves each slot before any await and holds it until rejection or a successful merge.

StartAgent and AgentStatus alone gain optional answering: DecisionRef, default null. It links
a new task to its originating inbox entry, copied unchanged into every status for that task so
events alone suffice in live observation and replay. This closes the open contract question in
"A decisions inbox is the form of Needs you" without changing ReviewCard answers, their person
principal, ChangeResolved.answers or any other event field or type.

The discriminated reference retains card identity (node_id, question, evidence_seq), scoped to
change_id, revision and head_commit; no separate card id is introduced. Change-level kinds are
checks_unavailable, follow_main, risky_area and examples_before_code. Uniqueness is one entry per
(kind, change_id, revision) for change-level kinds (several unavailable checks form one entry),
per (change_id, revision, node_id, question) for cards, and per (agent_id, revision) for lead plans.
The lead revision names LeadStatus, not a plan_id. evidence_seq is the event that raised the
entry, retaining a card's existing evidence_seq. All references are run-local. Candidate revision
and head prevent an older answer from consuming a newer candidate's entry. Pydantic validates
intrinsic shape; the producer verifies event existence, target equality and entry uniqueness.

A bounded compatibility scope exception keeps make check green: exhaustive frontend look maps
retain A/B exactly and use named C-F placeholders backed by existing neutral theme tokens, with
slot letters still visible. These placeholders are unreachable until the backend admits more
than two agents; line B's many-agents look replaces them with the maintainer's chosen colors and
lands before backend admission. File activity and a test filter use Slot, typed status fixtures
include answering: null, and the backend health test expects 1.6.0. No runtime behavior changes;
the answering echo belongs to item 9's backend front.

Safe creation, nullable SaveFile.base_hash, zones and inventory move from the reserved 1.6.0
bundle to 1.7.0 because they are not designed yet and must not hold these approved additions.
SaveFile stays unchanged. Delivery A decisions 11–12 remain unchanged as the approval history.
The exact 1.5.0 schema bytes are frozen before regeneration as the new tuple-check baseline,
with a hash assertion and both older baselines and independent negative controls retained.
Generated files still come only from make contracts and keep byte-for-byte drift checking.

## 2026-10-28 — Inbox answers, returns to authors and failed delegations

The maintainer approved these three rules at 19:27 ART:

1. A Yes is prepared, never saved alone. Approve and merge remains a separate, explicit
   action, so answering a question cannot accidentally merge code.
2. A No on a lead plan, or on examples without a proposal yet, returns to the author without
   rejecting. The author needs to revise the plan or examples before code or a reviewable
   proposal exists. This requires a command and plan or lead revision correlation: it belongs
   to line A and is not built yet.
3. A failed delegation reopens its originating entry, retaining its link and earlier attempts,
   because accepting a delegated task is not proof that the task delivered an answer. The
   first matching answering status is the echo; a later halted or stopped status reopens the
   entry unless that agent proposed after the echo. Done, a proposal, another active or
   successful delegation, a recorded answer or a resolution closes it as before. Repeated
   statuses retain one attempt per task. A run reset clears the history.

Rule 3 is a pure frontend projection in
[decisionDelegations.ts](frontend/src/state/decisionDelegations.ts), composed by
[decisionInbox.ts](frontend/src/state/decisionInbox.ts). Reopening retains the original identity,
evidence, first sequence and timestamp, so age and ordering do not restart. Completed attempts
retain the agent id, slot, end state, recorded reason and echo/end sequences in echo order.
Events never open a panel or move focus. The replay fixture is
[handwritten-inbox-delegation.jsonl](fixtures/events/handwritten-inbox-delegation.jsonl).

## Phone practice composition and collapsed evidence (2026-10-28)

The maintainer approved the phone sketch for the submission practice and then requested a
collapsed diff. Below 1280px this bounded, browser-only exercise departs from the IDE’s
widen-window notice and 560px graph minimum: a folded graph stays above scrolling evidence,
with equal neutral Approve/Reject choices below. The full IDE and desktop practice stay unchanged.
Next carries prose numerals, and compact graph labels retain the approved 14–16px scale.
This approved brief takes precedence over Impeccable’s generic typography and composition rules.

Start each decision with the exact change.proposed summary attributed to its agent: it is the
agent’s claim, not a server verdict. The lines chip opens all source evidence, including tests
and the called helper; Recorded task stays independent. The visitor can decide without opening
code, keeping the first phone view focused while preserving inspection on demand. The server’s
summary omits author declarations deliberately; these attributed recorded words must not be
presented as Oversight’s findings. No backend command runs and no recorded decision is exposed
before both answers. DESIGN.md’s Phone width section records this exception.

## 2026-10-28 — Agent C–F identity and one slot look map

The maintainer approved C violet, D pink, E sky and F plum on 2026-10-28. Final graphic/text
pairs, including the text approval on 2026-10-28 at 22:30 ART (2026-10-29 UTC):
C #a392d6/#a392d6, #4f3189/#4f3189; D #dba4c8/#dba4c8, #a2568a/#894674;
E #a1c7f8/#a1c7f8, #3475c4/#255ea1; F #ab6e9e/#b27ba7, #623659/#623659.
A/B are unchanged. DESIGN.md records the tint and node-fill recipe.
The contract Slot keys one exhaustive typed look map; components, graph layers/styles and field
emitters read it. Missing slot families fail typecheck. Admission remains two agents until the
backend's separate front. Overlaps, badges and folds for N agents remain a later graph front.

The coordinator measured each graphic at >=3:1 and text at >=4.5:1 over dark #101012, #151518,
#131313 and day #f8f7f3, #fcfbf8, #fbfaf7, #f4f3ef, #eeede8. Letters on graphic fill pass
4.5:1 using dark #17110b and day #ffffff. Closest CIEDE2000 pairs among A–F plus passed/failed:
dark 15.8 normal (D–F), 9.2 protan (D–E), 10.6 deutan, 9.6 tritan (passed–E); day 13.5 normal
(B–E), 9.4 protan, 9.0 deutan, 10.3 tritan. These are supplied coordinator measurements,
not measurements generated by the implementation's WCAG tests. Six agent hues cannot stay far
apart for every color-blind viewer without using reserved green, red or amber. The letter carries
identity; the same agent letter remains on cards, rail avatars, review and graph labels.

The field retains its eight-source cap. Sources sort by current focus before last read, then by
slot and agent id, and excess sources are dropped deterministically. The regression is
frontend/src/graph/field/signal.test.ts, "caps six agents at eight sources".

Day tokens are prepared from the existing DESIGN.md palette to verify both themes; this adds no
theme switcher and does not claim the later full day-mode front is complete. A/B demo snapshots
remain the visual baseline. Impeccable audit/polish preserves this approved identity; the pinned
palette and existing signal effects take precedence over generic craft-floor recommendations.

The supplied flat-background measurements did not cover composited tints or raised surfaces.
The original text values gave F/card tint 4.04:1 and F/raised 4.46:1 in dark, and day D/shell
tint 3.80:1 and E/shell tint 3.76:1. On 2026-10-28 at 22:30 ART the maintainer approved the
three text variants above so labels meet 4.5:1 on raised and on the agent tints. Graphics,
tints, node fills, filled-badge letters and A/B remain at their approved values.

This replaces the opaque D/E backing treatment in 0612105. Day D/E tints again use their
graphic RGB at 12% alpha; node and new-node fills mix 12% and 7% graphic RGB over day-card
in sRGB with rounded channels. Dark F keeps its 14% graphic tint and near-black node fills.
Dark F text now reaches 5.14:1 on raised, 4.65:1 on its tint over card and 5.44:1 / 5.77:1 on
node / new-node fills. Day D reaches 5.08:1 on raised, 4.63:1 on tint over shell and 5.44:1 /
5.80:1 on node / new-node; day E reaches 5.07:1, 4.65:1 and 5.47:1 / 5.84:1 respectively.

The original contrast tests keep every surface and threshold. The additional 0612105 regression
for opaque backings across every shell surface is retired with that treatment: tint over
raised is not used by the UI and gives D 4.42:1 / E 4.44:1 with the approved values.
The rendered card/rail/review regression from 0612105 remains. Exact-value tests pin the
three newly approved text tokens and guard the restored translucent tints and original node fills.

## 2026-10-29 — Item 9 questions are only for unasked or untied differences

The maintainer decided on 2026-10-29, as follow-up 3 to item 9 and the decisions inbox. This entry records the
parts that touch item 9's producers and events; line B records the card, the inbox and the header.

Questions are only for differences the task did not ask for, or that Oversight could not tie to the task: the
decision model's `not_asked_for` and `cannot_tell`. A difference classified `asked_for` never becomes a review
card. It goes into the server-written summary of what changed, so it is reported, never dropped.

Yes means the same on every question, including "Oversight could not tell whether this was asked for". A Yes
records that the person accepted the difference, never that the task asked for it. Both come from existing data,
with no new contract field: "asked for" only from the classification, which never has a card, and "accepted by
the person" from a card answered Yes by the person. The summary and proof 3's counts keep them apart. A card's
kind is read from its server-authored question, one of two fixed texts. When pinned examples exist (item 13), a
Yes on a could-not-tell question is the one offered as "keep as example".
## 2026-10-29 — The inbox header count lands on the maintainer's approval of its captures

The card and inbox shots gate holds every card or inbox front until the maintainer approves its
captures. For `frontend/inbox-count` he approved its four header captures and confirmed it to the
coordinator directly on October 29. An approval relayed by another coordinator session does not
count: it must come from him in the session that lands.

## 2026-10-29 — small_cone does not pass what it cannot see

Audit note: commit 13c08bc replaced the same-day entry "Small cones require no unobserved
possible callers" with this consolidated entry. It retains that decision's unknown-on-unmeasured
count, shared caller definition and both measuring producers; this is not a new maintainer decision.

The impact cone follows exact calls only, so an empty or small cone can mean "nothing calls this"
or "the graph could not tell who calls this". On Boltons most functions have no exact callers, and
many of them have callers the graph marks as possible. `small_cone` used to pass both cases.

The maintainer decided that the rule answers `unknown` when a changed node or a node in its cone has
a caller linked only by possible calls, or when that count was not measured. The count is exactly
the review's "N possible callers, not followed": distinct non-test callers outside the changed nodes
and the cone, with a possible call into them and no exact one. The proposal assessment and the
merge-time re-measure both measure it. `unknown` never counts as passing, so in rules mode the
change waits for the person, and the inbox entry lists those callers. His Yes is recorded as
accepted by him, never as asked for, as with D6. A cone that already fails the exact limits stays
`fail`. The contracts do not change: the recorded observation keeps its five counts. Planning
estimates for Boltons d31d821, not reproducible from this repository (the corpus measurement and the
drafted task list are not checked in), put 446 of 898 single-function changes and 5 of the 16
drafted proof 2 tasks in this case. The tasks are not changed to avoid it, and the run reports
whatever share results.

## 2026-10-29 — Standing grants: Oversight remembers what the person let go

With the `small_cone` fix, proof 2 would ask the maintainer about most Boltons changes, often the
same question about the same module. He decided that Oversight remembers what he authorized.
Three layers decide whether a change waits, each in one direction: rules pass or wait with
evidence; Muse Spark Open's decision can only add a wait; the person's grants can only remove a
wait the person could accept themselves.

A grant comes only from the person's own answer to an inbox entry (Yes plus "don't ask again",
with a scope they pick), never from an agent, a lead plan or a message relayed by another session.
Free-text grants may come later, always turned into a structured grant he confirms.
It can waive possible callers not followed or a protected path, never failing tests, conflicts,
test integrity, cross-review or a decision question. It applies by exact scope match, never by a
model's judgment; its scope is narrow (one reason kind, for one module, path, agent or run) and it
ends with the run or the session unless he chooses otherwise. Grants are listed with Revoke,
a change merged under a grant says so on its card, with the grant and when it was given.
Every use is recorded as evidence, and proof 2 counts questions asked and grants applied
separately. Building waits for his approval of the plan in `docs/plans/standing-grants.md`.
Using his past answers as
examples for the open model is later, with its own bench arm.

## 2026-10-29 — Standing grants approved as contracts 1.7.0; the creation bundle moves to 1.8.0

The maintainer approved the plan in `docs/plans/standing-grants.md` and its contract change on
2026-10-29, in the coordinator's session. Review then found that 1.7.0 was already reserved for
safe creation, nullable SaveFile.base_hash, zones and inventory (the 2026-10-28 contracts 1.6.0
entry and `docs/proposal.md` section 4.9). He decided that grants take 1.7.0 and ship first, and
that safe creation, nullable SaveFile.base_hash, zones and inventory move to 1.8.0. The reason is
the one from 2026-10-28: they are not designed yet and must not hold an approved addition. No
schema has shipped under 1.7.0, so no version string names two schemas. The earlier entries stay
as the approval history.

## 2026-10-29 — Proof 2 measures correctness with hidden checks and a paired arm

Proof 2's counts said how many changes were accepted, not how many were correct, and nothing
compared Oversight with agents that merge on passing tests alone. The maintainer approved adding
both on 2026-10-29, in the coordinator's session. Each proof 2 task gets hidden checks, written and
frozen before the run without seeing any agent output, and kept outside the product workspace as
`tools/eval` already does. The same run then yields two arms judged by those checks: without
Oversight, each agent's first proposal with passing tests, merged as it is; with Oversight, what
reached main after rules, questions and revisions. Model, tasks and generation are identical, so
only the gate differs, at no extra model cost. Each arm reports correct changes on main,
regressions that got through, questions per accepted change, questions the checks show were not
needed, and cost and wall time per correct change; questions and grants applied stay separate.
The reporting rule is fixed before measuring. The arm without Oversight does not model an agent
that never receives Request changes, and the report says so. Line B owns `tools/eval` and builds
it after the maintainer approves the proof 2 tasks.

## 2026-10-29 — Keys typed into a packaged app wait for after the hackathon

Oversight runs locally from the repository with its own `.env`, and judges open the static build,
which has no backend and no keys. A packaged app would take keys in Settings instead: write-only,
kept by the local backend in the system keychain or a user-only file, never returned to the
browser, events or recordings, with `.env` kept for developers. That needs the secrets rule in
AGENTS.md changed by the maintainer, a contract change, and an independently reviewed security
plan, since a server on 127.0.0.1 must refuse requests from other sites open in the same browser.
On 2026-10-29 the maintainer placed it after the hackathon: it serves none of the three proofs,
and no judge types a key.

## 2026-10-29 — Aggregate counts on demand and measured frame titles

The maintainer approved both fixes on October 28 at 23:52 ART (October 29 UTC).
The route-cost audit of the dark Boltons preset at 1920×1080 found 101 aggregate labels touching
another label or header at the start camera (180 label pairs, 47 label/header pairs), and 211
at 0.2 zoom (448 and 119 pairs). Functions at 0.2 also put `NamedTupleInpu…` against `4 methods`.

Replace always-visible aggregate counts with immediate labels on the hovered aggregate and the
selected node's aggregates. Keep the directed edges, arrows, counts, routes and camera unchanged;
edges remain unselectable and this interaction takes no focus. DESIGN.md records this departure.

Replace average-glyph title and count estimates with cached canvas widths from the loaded
Atkinson title and count faces. Reserve the count's actual width, the existing gap and the folded
chevron inset before ellipsizing; preserve fonts, frame sizes, header layers and clipping.
Graph creation waits for these faces; a recovered font load rebuilds early fallback layers once.
Without font/canvas measurement, tests retain the estimate. Row state-word clearance uses the
same measurer because it needs only the existing word face and size.


## 2026-10-29 — Exact calls at rest, possible links as quiet context

The maintainer approved the resting module rule at 09:05 ART, its open-module extension at
09:20 ART, and the straight-line treatment at 09:42 ART on October 29 (12:05, 12:20 and 12:42 UTC).
Keep directed exact links with their taxi routes and arrows. Count exact calls separately; possible
calls never make an exact bundle thinner. Possible-only pairs without an exact reverse may keep
one very faint straight line, with no arrow, when any callee name survives the Python 3.12 built-in
attribute-name filter. The sorted names and generation command are committed. This is a drawing
heuristic, not a new assertion about call resolution, and it never changes impact or underlying counts.

Boltons has 34 non-test modules, 227 directed pairs and 1,530 cross-file calls: only 13 pairs
(13 calls) are exact. Of 214 possible-only directed pairs, 10 survive the name filter, representing
9 unordered pairs. Rest shows 13 exact links and 9 faint lines; 16 modules have no line. The recorded
demo has 7 directed pairs, 4 exact; its 3 possible-only pairs all survive (orders to cart, inventory
and models). Open views keep only exact calls at rest, even on agent-lit nodes. Hover, selection
and displayed cones reveal possible calls without filtering; cones still follow exact calls only.

This replaces bundling every call into routed module links and drawing all cross-file calls in
open modules, the source of parallel gutter lines. Folded cards and graph text plates occlude straight
lines; open frames retain their translucent body fill, so lines can show through it beneath the rows.
This deliberately departs from the Modules sketch's lines across text. Labels disclose exact
and possible counts on demand. Pointer hover lifts the existing path and arrow in a neutral token,
with no motion, width change, camera move or focus transfer. A hidden line supplies no marker route.
Fixture checks read the optional offline export at `.runtime/graphs/boltons-d31d821.snapshot.json`
and skip Boltons cases only when it is absent; provenance and regeneration are in
`fixtures/layouts/README.md`. No Boltons snapshot is committed. Same-module exact calls remain
hidden during hover, selection and lighting, preserving the prior behavior. The graph heading,
legend, zoom controls and Modules/Functions/Details control (including zoom percentage) use fully
opaque `panel` plates to mask lines behind their text. Other graph overlays retain frost.
Screenshot logs use .events rather than .jsonl so generated cuts do not enter the repository-wide
recording validation. This required tooling deviation touches `frontend/shots/run.sh` and
`frontend/shots/capture.ts` outside the original `frontend/src` and design-document scope.
## 2026-10-29 — Count final decisions per change and keep their entry point visible

The maintainer approved one inbox entry for the latest pending revision of each change, containing
its questions and other decisions. Answers, card answers, answering echoes and delegations keep
that entry pending; delegation attempts remain history. A completed Approve, Request changes or
Reject removes it at change.resolved; a merge conflict still needs action. A new revision after
Request changes is pending again. Decisions without a change, such as a lead plan, remain single
entries. This replaces the earlier answer-or-echo closure rule and rule 3's hide/reopen behavior,
while retaining its delegation history, original ordering, event-clock age and run reset.

The header is the inbox entry point, always in the same place and at the same height: "Nothing
needs you" at zero, in muted gray without a lamp or badge; "Decisions · N" otherwise, with a neutral
count in tabular Mono. Its accessible name includes N. Until the inbox view exists it opens the
current review queue. The agent card's "Ready for review" pill stays unchanged.


## 2026-10-29 — Show possible callers the exact cone does not follow

The maintainer approved this wording on 2026-10-29 at 09:39 ART, before proof 2 starts.
An empty exact cone cannot establish that a change affects nothing. Review and live diff now
state the one-hop possible callers outside the seeds and bounded cone, excluding tests and
nodes with an exact call into either. The count uses the change overlay for live and proposed
changes. DESIGN.md records the approved empty/non-empty texts and neutral presentation.

On Boltons d31d821, 726 of 925 library functions and methods have an empty exact cone;
356 of those have possible callers. A measured built-in method name filter was rejected:
it hides every possible caller of 310 methods (OrderedMultiDict 42, IndexedSet 25,
BarrelList 24), leaving 278 of them apparently without callers at all. Count every possible
call here, regardless of name; do not follow these calls or imply the cone is complete.

## 2026-10-29 — Repository memory is a candidate: only what search cannot find

The maintainer proposed what Oversight should remember about a repository, recorded as a candidate
and not queued; nothing is built before the freeze (docs/additions.md, "Repository memory"). Search
finds what code says, never the decisions, invariants and reasons that are not written down, and it
neither enforces a rule nor notices a stale one. So the memory holds only that, each kind in the
mechanism that fits it: executable pins, text pins anchored to nodes, the repository's own AGENTS.md
and skills, and standing grants. Facts from running something wait for a later version, with command
output as evidence. Text pins load deterministically; Muse Spark Open can only stop or flag; only
the person creates or confirms a pin. Text pins are built only if a measured Boltons trial after
the freeze shows fewer rule contradictions at equal or lower cost.

## 2026-10-29 — Item 13's pin record carries a kind and a node anchor

Checking the reserved shape showed that `ExamplePin` is a confirmation stamp inside a task example,
so it cannot hold a text rule without faking an example's inputs and expected result. The
maintainer decided that item 13's pin record is born with `kind`, only `example` for now, and a node
anchor, so that a later text rule is one new kind rather than a second pin family; `ExamplePin`
stays as it is. Because a node anchor breaks on a rename, a pin whose anchor no longer exists shows
as orphaned, in the Pinned lens and as an inbox entry, and never vanishes silently. A new enum value
is not a minor change under proposal section 4.9 today; the request to make it one is his to decide.

## 2026-10-29 — A new enum value is a minor contract change, under conditions

Section 4.9 of the proposal listed a field with a default, an endpoint and an event type as minor
additions, but not a new enum value; 1.4.0 needed a bounded exception to extend reserved enums,
and item 13's pin `kind` would need another for a later text rule. The maintainer approved a
narrow amendment on 2026-10-29: a new value in an existing enum is minor when every existing
payload and fixture still validates and every TypeScript consumer of the enum is exhaustive, so
the compiler forces the UI to handle it, as with a new event type. A consumer that is not
exhaustive is made exhaustive in the same change, or the change is not minor. Strict models are
unchanged, and removing or renaming a value stays 2.0. This answers the request left open in the
entry above on item 13's pin record, so its later text-rule kind can ship in a minor version.

## 2026-10-29 — Python consumers of a contract enum are exhaustive too, and merge decisions fail closed

The first enum amendment required exhaustive TypeScript consumers only. The backend also branches
on contract enums, such as rule results, merge modes, file statuses and review verdicts, and there
a value nobody handles can fall into a pass branch, as `small_cone` once passed callers it could
not see. The maintainer amended the rule the same day: every Python consumer is exhaustive too,
with a `match` that ends in `assert_never` or a table keyed by the enum, checked by pyright, or a
test where pyright cannot check it; and code that decides a merge, a rule result or whether a
change waits fails closed, so an unknown value makes the change wait. A read-only survey of
`bb64e65`, counting one consumer per file and enum, found 152 of 153 Python consumers not
exhaustive; 39 of them decide a merge, a rule result or a wait, and in four code paths across five
enums a new value would pass: a test file's new file status, a new test case outcome after a
failing run, a new cross-review verdict, and a new call-edge kind (`EdgeKind`) or confidence
(`Confidence`). No existing value passes this way; the maintainer decided to make those four paths
fail closed in a separate change after standing grants land. He approved the first amendment's
request and its final change in the coordinator's session, not through another line.

2026-10-29: The four merge-deciding families now fail closed with explicit enum tables and
completeness tests: existing-test file statuses, before/after case outcomes, cross-review
verdicts, and impact call-edge kinds/confidence. Unknown values touch an existing test or leave
the case/review/cone unknown, so they cannot make the change pass its rules.

## 2026-10-29 — Contracts 1.7.0 — approved by the maintainer

The maintainer approved `docs/plans/standing-grants.md` and its contract change on 2026-10-29,
in the coordinator's session, as recorded above under "Standing grants approved as contracts
1.7.0; the creation bundle moves to 1.8.0". This version adds the structured grant request on
Approve, person-created and revoked grant events, and immutable grant-use receipts on rules
resolutions. Existing 1.6.0 recordings and commands remain valid with absent new fields.
The revoke command is POST /grants/{grant_id}/revoke, with no body.
Contracts 1.7.0 deliberately omit the plan's `grant.expired` event: run expiry ends a grant
with its run. Adding that event later requires another minor version.

The first backend supports possible_callers, exact module or run scope, and run expiry only.
Unsupported reasons, scopes and expiry are refused rather than silently broadened. A grant
removes only the measured possible-callers unknown; it never changes the recorded assessment
or waives tests, a failed rule, missing measurements, or another unknown. Server authorization
is rechecked at every rules barrier and serialized with revoke at the Git effect boundary.
The creation bundle remains reserved for 1.8.0.

## 2026-10-29 — Evidence-backed protected-path evaluator; activation stays closed

The coordinator approved a built-in category table for the reserved `protected_paths` rule,
based on the four categories in `docs/additions.md:270`. The maintainer may change its patterns.
`changes/_protected_paths.py` records manifests/lockfiles (`pyproject.toml`, `setup.py`, `setup.cfg`,
`requirements*.txt`, `uv.lock`, `poetry.lock`, `Pipfile`, `Pipfile.lock`) and test configuration
(`pytest.ini`, `tox.ini`, `noxfile.py`, `.coveragerc`, `conftest.py`) by basename at any depth.
CI (`.github/**`, `.gitlab-ci.yml`) and security (`.env`, `.env.*`, `.gitignore`,
`.pre-commit-config.yaml`, `.gitleaks.toml`) match workspace-relative paths from the root.
Matching uses case-sensitive Python fnmatch semantics; there is no filesystem or directory
expansion. Explicit list snapshots instead use exact membership, including literal glob characters.

The pure evaluator requires complete snapshots and separate evidence identifying the table/list
version and the changed-file source, retaining added/modified/deleted statuses. Missing or
unreadable sources, missing receipts, incomplete inventories, malformed paths or unknown statuses
produce unknown before counting, even beside a known hit. Captured empty lists and inventories
are distinct from unavailable sources. Each distinct matched file counts once; a complete,
evidenced hit fails and zero hits pass under the existing fixed CheckInputs threshold.
Private results retain every hit's category and pattern. Contract assessments retain only counts,
completeness and references to both sources, qualified by each hit's path; those snapshots allow
the private diagnostic to be reproduced without adding fields to the wire format.

This adds a callable evaluator, not live enforcement: the scheduler still selects exactly
`safe-merge-probes-v1`, both merge predicates reject the paths profile, protected-path grants stay
refused, and startup capability remains closed. Source capture/loading, settings, profile
migration, activation and any future waivable-hit semantics require separate work.
## 2026-10-29 — Internal object-only preflight for a rules merge's inverse

`changes._revert_preflight.preflight` accepts a server-supplied resolution event (or its stored
envelope), a `RevertChange` and the target change ID. Store lookup and HTTP remain separate work;
Revert stays reserved. It revalidates provenance and the recorded assessment, including pass
observations and grant coverage, and refuses unknown or inconsistent evidence.

Classification holds main's lock and the shared Git lease, with one finite deadline covering lock
waits and guarded asynchronous Git. It captures main's commit H, requires the caller's observed
main and attached HEAD to agree, and refuses dirty, hidden-index or in-progress checkout state.
The recorded merge M must exist in H's ancestry, have the assessed main P and proposal as its
exact ordered parents, and match the assessed tree and rules commit identity/message.

The inverse is `git merge-tree --write-tree --merge-base=M H P`. A successful single tree OID,
its object type, strict object verification and a final unchanged-main observation are all
required for `clean`. Unsupported plumbing, conflict, timeout, truncated/malformed output,
missing objects or other uncertainty becomes `conflict`; cancellation propagates after process
cleanup while locks remain held. Strict Git object verification may inspect other stored objects
and shares the same deadline, so unrelated corruption or an oversized repository can also refuse.
No refs, real index, checkout files, operation markers or worktree registrations are changed;
only unreachable Git objects may be created. There is no mutating fallback. A clean result proves
textual applicability against captured H, not semantic correctness or authorization for a later
effect. A later Revert transaction must establish its own current evidence and authorization.
## 2026-10-29 — Witnessed assertion-loosening run findings

Package 5 now publishes only `kind="loosened"`: a resolved individual integrity fact,
its agent-authored assertion edit, an earlier observed failure, and a later explicit
per-case pass. The bridge reuses the temporal core but strengthens its absence-of-failure
result to an observed pass; skip, deletion and uncollected cases establish no pass.
The exact pytest id includes class and parameter identity. Broader temporal kinds remain
reserved, and this producer enables no profile, rule authority or automatic merge.

Evidence uses committed `tests.executed` and `change.updated` receipts, never tool-step
numbers or adjacent global events. Receipts carry ownership and are retained at existing
publication boundaries through cancellation. Only agent executions and the current
proposal verification participate; probes and combined runs cannot witness recovery.
Each proposal freezes its observations, and comparisons stay within that proposal round.

An edit compares the immediately preceding observed source with its published modified
source using the existing detector. Cumulative base-to-head findings alone cannot name
an intervening weakening. Duplicate publications and human edits supply no agent edit
witness; proposal republication supplies no new source-edit witness. Bounded source reads
(at most 200 reported paths per execution) use the captured Git tree in the lock-owning
task. Both executions must have the exact corresponding file version; intervening edits
in that file conservatively leave a comparison unknown. No whole-log infrastructure or
runtime/provider protocol is added.

A listed, observed failure remains usable from a truncated initial failure list. Recovery
requires a finished run, a complete failure list and complete retained per-case evidence.
Missing receipts, versions, identities, runs or passes retain explicit uncertainty, alongside
independent proven findings. Validation recomputes the exact projection and edit references
from the frozen capture before publication. Zone/reach and other producer diagnostics
survive, facts remain incomplete, and an empty finding list grants no merge authority.

## 2026-10-29 — Current-run history pages have a fixed horizon and a 1 MiB wire budget

`GET /history` now reads committed envelopes from the current event store in live and server
replay modes. The first request captures the store's acknowledged committed sequence before
awaiting disk IO; continuations resend that `through_seq` and use `next_seq` as `after_seq`.
Later appends cannot change that traversal. ASCII integer queries are bounded to 18 digits;
invalid bounds are rejected rather than clamped. Defaults are after 0, limit 50 and the captured
horizon, with limits from 1 to 100 as reserved in the proposal.

The reader uses a separate read-only SQLite connection in a worker thread, filters by the exact
`HistoryPage` event-type allowlist before pagination, and reads at most limit plus one sequence
and byte-length records. It fetches only envelopes that fit. Original envelopes and provenance
are preserved, without consulting live command state or granting decision authority.

This front introduces an implementation policy, not a contract constraint: the complete serialized
UTF-8 success body, including metadata and punctuation, is capped at 1 MiB (1,048,576 bytes).
A page may therefore contain fewer than the requested limit. Its cursor still names its last
returned event when an eligible successor exists within the horizon, so no event is skipped.
If the first eligible envelope cannot fit intact, the response is the existing typed `too_large`
413; payloads are never truncated. Unexpected disk or validation failures use the existing
`internal` error path. No index or schema migration is introduced.

## 2026-10-29 — Phone decision disclosures read as list rows

The maintainer wants full-width touch rows that read as a list, rather than empty buttons
competing with Approve and Reject. Recorded task and Possibly affected share divider hairlines,
verbatim task and shared graph-count previews, and down chevrons; their content expands in place.
The diff's disclosures keep their existing boxes. Consolidating the rows and tightening the
summary-to-list gap keeps the collapsed last row above the dock on first view at both phone sizes,
without a guessed dock-height padding. Desktop composition and typography remain unchanged.

## 2026-10-29 — Source profiles keep pytest options apart from targets

Source profiles separate `pytest_options` from positional `pytest_targets`, so selected case
reruns retain collection options while replacing only the targets (including Boltons doctests).
## 2026-10-29 — Scale and depth are separate axes; the map waits for a sketch

The maintainer approved the [Scale and depth definition](DESIGN.md#scale-and-depth) on 2026-10-29.
The built Modules / Functions / Details control (`bfe7a18`) sets how much unfolds; zoom sets how it
is drawn. The old middle scale level, function dots inside module frames, is dropped. The map
would enter automatically when `frame-title-size` (17px) times zoom falls below the existing
11 CSS px floor, below 64.7% (11/17), and leave above a slightly larger size set by the sketch to
avoid flicker. Fit chooses cards at or above that leave threshold and the map otherwise; "Map"
appears beside the percentage, with no fourth button.

The map and folder groups are not built. Modules become circles at their reserved block centers,
with area proportional to function count. Only the map groups by folder, at the first level that
splits the repository into at least two groups, with tests separate; never computed clusters.
Groups with fewer than two modules and flat repositories draw no frames. Cards stay as today,
but continuous folder frames require a preset rearrangement that also moves card positions;
the sketch must show this for the build decision. Group summaries use team-colored rings,
neutral pending-decision counts and cone reach. Links keep the call-visibility rules
([DESIGN.md "Call visibility"](DESIGN.md#graph-nodes-and-their-states-signature-component)): exact
links at today's weight; possible links that survive the built-in name filter, drawn as faint lines.

The [Boltons `d31d821` preset](fixtures/layouts/boltons-d31d821.json), the proof 2 repository,
contains 66 modules by top folder: boltons 30, tests 32, misc 3, docs 1. That gives a large library
group, tests and misc, while docs draws no frame. Fit is at the threshold without tests (65%) and
lower with them. This remains in [Undecided candidates](docs/additions.md#undecided-candidates):
define now, then sketch with real Boltons and demo data, and the proof 2 repository if it changes.
The sketch shows Boltons fitted with and without tests and identifies the resulting level.
Whether to build is decided with that sketch in view, never before the Saturday checkpoint;
approval of the definition does not approve the build.
## 2026-10-29 — Behavior inputs follow unique module aliases and require opaque arguments

Behavior seed discovery accepts a direct module-level `name = module.function` or
`name = imported_function` after its import, including annotated assignments. Both the alias
and its imported root must have exactly one module binding. Rebinding, deletion, conflicting
imports, star imports, global declarations and call-site shadowing keep the alias unresolved.
The detector's binding visitor and lexical shadow checks are reused; alias chains, conditional
alias definitions and aliases inside functions remain outside this rule. This recovers literal
seeds without importing or executing the project.

A nonliteral parameter default is an internal opaque marker, never a generated value. Every
compared input must provide that parameter explicitly on both sides, using only the existing
literal calls and value pools; otherwise the candidate is discarded. No sentinel value is guessed
or evaluated. Literal defaults still participate in pools and effective-argument identities,
including unequal defaults on the two sides. Literal size limits still reject oversized defaults.
Input/row caps, deadlines, isolation, fresh repetitions, fingerprints and unsupported function
kinds/effects are unchanged; the public `probe unavailable` projection remains unchanged.

Temporary tests reproduce only the short `windowed`, `windowed_iter` and `ellipsize` signatures
and the alias spelling from Boltons d31d821, with bodies written here. At that pin the actual
`ellipsize = strutils.ellipsize` line is local to `test_ellipsize` (line 398), so the module-level
fixture exercises the requested rule without claiming to resolve that local upstream alias.

## 2026-10-29 — Detection model arms v2 preserve tests-only identity

The detection pilots at 19:52 ART used a 512-token completion allowance: detect-2 consumed
all 512 tokens with no usable classification, consistent with reasoning truncation documented
by the API probe. Detect-3/4 targeted methods unsupported by D9, made no call and proved
nothing about classification. Model arms therefore get new IDs detect-2-v2, detect-3-v2 and
detect-4-v2, with explicit task/diff, behavior-Spark and behavior-Spark-Open roles. Their pilots
use top-level development targets d02, d01 and d02. A model pilot without a sent call and
bounded diagnostics refuses. Diagnostics record validation stages and metadata, never reply text.

Each model arm hashes and sends the product D6 completion allowance (currently 4096), retaining
the strict 512-character response validator. Since the product factory exports no allowance
constant, evaluation imports it and extracts its single positive literal through AST inspection,
without executing it or loading credentials. Imported ReviewCardLimits defaults supply the
same per-call and total deadlines to configuration and runtime (30 and 120 seconds). Admission
uses the greater of observed pilot cost and the declared calls/input-token worst case at the
configured completion allowance. Old pilots are listed as superseded and keep counting toward
cumulative spend. Detect-1 retains its byte-identical v1 configuration, fingerprint and finished
pilot. Frozen patches and labels do not change: b09 Inventory.reserve, b14 Customer.email
setter, b17 Cart.add and b19 Product.label remain unprobed questions by D9, in both denominators.
No paid run or provider probe is part of this change.
## 2026-10-29 — Life layer, part 1: contours at rest, light only while acting, merge travel

The maintainer approved the life-layer look on 2026-10-29, after the sketches in
docs/sketches/life/ (committed as an exception for approved sketches only; the interactive
reference stays outside the repository). This part builds points 1 to 6 of his decision; the
tests scan, rolling numbers, top-edge light, focus glow and button press are part 2.

1. **Colored light means an agent is acting now, and it belongs to a change in three states.**
   (a) Working: the agent's status is working or revising; today's light, with its ring where it
   reads or edits. (b) Waiting: the change has at least one open entry in the decisions inbox, read
   from `selectDecisionInbox(state)` and not from a rule of the field's own; the light stays on
   the change's node, steady and dimmer (`waiting-light-weight` 0.5, a theme-independent token, so
   both token sets carry it), with no motion, and never pulses, blinks or grows however long it
   waits; opening the review or clicking the node does not turn it off, closing the entries does.
   (c) Decided, whether merged (by rules or by the person), approved or rejected: a decision fades
   only a light that exists. The afterlight starts from the light the change had on its node at
   that moment (waiting, or a working agent's) and fades out over about 90 seconds, along
   `(1 - p)^1.6`; a change with no light then gets none (a rejection of it lights nothing), and a
   merge still sends its pulse and brings its light as the pulse arrives. At rest nothing is lit. No color or pulse is added anywhere else: the "Decisions · N" counter stays neutral, and
   what changed earlier stays told without color by the rail cards and the node marks. This
   replaces the rule that an agent's current node and last read stay lit until the change is
   decided, and the 240ms fade of the light on approval. Ambient motion and light without events
   stay allowed. The first version of this entry gave an agent waiting for review no light and asked
   the maintainer; his clarification of the same day is state (b). Every proposal now opens an
   inbox entry, so the recorded runs show the waiting light between the proposal and the decision.
2. **The rest ground is gray topographic contours derived from the graph:** one moderate hill per
   module (its height grows with the calls into it from other modules, over a small range), a few
   soft mounds in empty areas, wide level spacing (5 to 7 lines around a lone hill, never more than
   8; the maintainer asked for the sketch's density at 13:32 UTC), 1px, very
   faint, fading outward, drifting slowly and continuously (a change of shape takes minutes),
   masked under overlays, still under reduced motion, gray only. It replaces the faint 28px lamp
   grid at rest: lamps now draw only where light reaches.
3. **Out:** click waves, the hover flashlight, borders that follow the pointer, button flashes, a
   boot sweep (at most a 0.5s fade-in) and view-change wipes (a plain crossfade). None existed on
   main when this was recorded.
4. **In, part 1:** the merge travels: a pulse leaves the agent's rail card, runs along the graph's
   edges to its node with a short fading trail, then the node's light fades as in point 1, with a
   soft falloff and never a hard or blocky edge (merge-mid.png's hard top edge is a known flaw).
   The maintainer clarified on 2026-10-29 at about 21:17 ART that the travel also lights the LED
   field, as in the sketch: six points, the head plus five every 20px behind it along the actual
   path (corners included, clamped at the start), with intensity `0.95 * (1 - k/6)^1.4` for
   `k = 0..5` and radius 19px. At most 12 points contribute `i * exp(-d² / (2r²))` before the
   common four-level 8×8 dither and agent-color ramp; only cells within three radii are touched.
   These keep the same 7px square LEDs and drawn lamp masks (overlays and header bands). The SVG
   line and head stay as they were; points end at arrival, and add no idle animation. Other
   milestone emitters are deferred. It never moves the camera or takes focus. Reduced motion:
   no travel or point lights; the node light appears and fades.
5. **Build:** one WebGL2 canvas draws the lamps and the contours; the heights are computed on the
   CPU at a coarse grid and sampled by the shader, so the contour cost does not grow with the graph.
   Without WebGL2 the graph keeps plain ground and no contours. Text keeps 4.5:1 and never sits on
   the lines, agents' colors mean agents only, and the real recorded run label stays.
6. **Sketches:** rest-contour, rest-day, rest-rm, merge-mid and life-v2.webm are in
   docs/sketches/life/ and linked from DESIGN.md as the target for the rest state and the life
   layer. DESIGN.md records which earlier lines each decision replaces (Dot field and rings,
   Motion, Reduced motion, the departures table). Where the build differs from a sketch, the
   departures table says so: no "merged" chips on nodes yet, and the contour mask is cell-wide.
7. **Build departures:** with contours on, an off lamp is not drawn (a day-mode change: the paper
   no longer shows a dotted grid at rest), because the lines need the ground bare. The lines fade
   under a light by its smooth glow (a second grid beside the lamps, which the golden lamp test
   leaves untouched), so the light's pool never cuts them in blocks. One slow timer (every 500ms)
   repaints the drift and the light's fade; animation frames run only while a pulse travels, a
   ring plays or a transition runs.

Reason: light that is on at rest stops telling who is working; a ground that carries the repository's
shape gives the idle screen something true to show without competing with the graph.

## 2026-10-29 — Life layer, look fixes from the maintainer's side-by-side captures

The maintainer compared the part 1 captures with the approved sketches and asked for four changes.
Each is built from tokens; DESIGN.md says which earlier lines each replaces.

1. **The dark ground is #070707, not pure black.** It is the `ground` token (and `background`,
   which mirrors it) and `lamp-off`, so an unlit lamp is the ground. Without WebGL the ground is
   #070707 too: he reads "plain black" in AGENTS.md as near black, and AGENTS.md is unchanged.
   This replaces DESIGN.md's `ground: "#000000"`, `lamp-off: "#050505"` and the "Black ground" key
   characteristic. Panels, rows and text contrast were checked: the token tests still hold.
2. **Contours at the sketch's level.** The brightest lines are about 25 to 28 on #070707 (they were
   about 11 on black), the median line about 23, and the lines cover about 4 to 6% of a clear area
   (the sketch's: 6 to 7%; it was 2.7%). The line is a soft 1px (whole within a tenth of a pixel of
   its center, gone 1.1px past its edge) at white 9%; each hill spreads over its frame
   (`contour-hill-spread` 1, from 0.78), so rings show outside big cards. 5 to 7 rings per lone
   hill, gray only, masks unchanged. This replaces "white at 10% in dark" in DESIGN.md's rest
   background.
3. **The busy light is four dithered levels again, in a color ramp.** Round 2 made each lamp's
   brightness continuous (the intensity raised to a gamma). That read round, symmetric and flat, and
   its premise was wrong: it took the sketch for a smooth glow, when `life.html` quantizes the
   light to four levels with an 8 by 8 ordered dither (`floor(clamp(tot, 0, 1) * 3 + bayer8)` over
   3). Near the core every lamp is whole; further out the dither leaves a regular pattern of single
   lamps, and that pattern is the LED character. The levels and the matrix return exactly as the
   sketch computes them (`dot-level-count` 4, `dot-dither-size` 8, no half-step offset), and
   `lamp-gamma` is removed. Each light keeps its own smooth cut before the lights add up (the
   sketch's `smoothstep(0.08, 0.22, I)`: `lamp-cut-low`, `lamp-cut-high`). The color ramp stays and
   now applies per level: the first level leans to the deep end of the agent's color, the second is
   close to its own, the whole level is lighter (`lamp-ramp-deep`, `lamp-ramp-mid`,
   `lamp-ramp-hot`), so agent B gets the same in blue. The far tail of the falloff is the sketch's.
   The lights follow the rows the agents touch, as the sketch's: each agent's current row at full
   weight and its last read at 0.4, each its own box (a method of a folded class is lit through the
   class's box), with lamps hidden under cards. On the sketch's own busy scene the product counts
   lamps at the four levels within a tenth of the sketch's (a test). Cost: the dither's cut is
   slightly higher than the continuous one (a lamp needs about 0.10 of one light, not 0.08), so the
   decided fade's halo outside a module card is gone about 35 to 40 seconds into the 90; the node's
   marker and the rail still tell the rest.
4. **The merge trail is short.** The orange right-angled line the maintainer saw on the right edge
   was the merge trail: 200px behind the head. It is now about 100px (28px bright, then 72px that
   fade with the square of what is left), and it is gone 350ms after the pulse arrives.
5. **Module frames are translucent, and only their headers are masked.** The sketch's frame is
   `rgba(12,12,14,.62)` in dark and `rgba(255,255,255,.5)` in day, its class `.6` and `.4`, and its
   masks cover frame headers, class headers, chips and overlays only, so the light around a lit
   row shows through the frame's body. Ours was 90% opaque and hid the second and whole levels
   there; the histogram over the cells outside rows could not see it. `module-opacity` is now
   0.62 in dark and 0.5 in day (a token, the day palette overrides it), and the field masks
   what the sketch masks: the header band of an open module or class (the padding above its first
   row, 54px and 44px, padded by `header-mask-pad`, 6px) and the exact box of a folded card,
   read at every draw so they follow the camera. A lit row's name and state word stay at 4.5:1 or
   more over the brightest lamp beneath them, a test over both themes, both agents and the
   frame label were it unmasked: the lowest is 4.93:1 (day, agent B's frame label). The header and
   folded-card masks apply to lamps and lines only: the first cut agent B's marker in half on the
   folded Inventory card, and markers and travel keep the overlay masks they had (the sketch's
   marker is a DOM element, life.html:80, and its pulse canvas sits above everything, life.html:219;
   neither is masked by a frame header, life.html:644 masks only the lamps).
6. **The editing row is tinted with its agent's color.** It already wore the sketch's wash (dark
   `#1e0c04` and `#060c1e` at 95%) from the `agent-a-node` and `agent-b-node` tokens; the capture
   shows it for A (a row) and for B (a folded class card, since a method of a folded class is lit
   through the class's box). Day uses a pale peach and a pale blue, opaque, as the sketch's day
   keeps its dark wash, which reads as a defect there.
7. **The histograms count the frame bodies.** The comparison with the sketch now covers every
   cell that no row, folded card or mask hides, so the translucent bodies count. On the sketch's
   own busy scene the product draws [20923, 943, 306, 181] lamps at the four levels against the
   sketch's [20977, 905, 307, 164]: the first level within 5%, the second within a lamp, the
   whole level 10% above, because the watch gain is 0.92 against the sketch's 0.9 (at 0.9 the
   product counts 168). The product's own captures differ in geometry: agent A alone
   [25011, 351, 76, 25], A and B [21406, 1027, 351, 80]. The field there equals the model run on
   the same boxes (a window around agent A's row counts [0, 35, 114, 53] in both); against the
   sketch's layout the second level is lower because the rows' rendered boxes are 48px tall
   against the sketch's 46, and the whole level, a ring one cell thick, changes with where the
   box's edge falls in the 7px grid.

Reason: the screen must show at a glance which agent is working and where, and the ground must
look the way the approved sketches look; every number is a token so the look can be tuned without
code.

## 2026-10-30 — Detection bench evidence fixes precede other line-A work

At about 23:20 ART on October 29, after the main bench run, the maintainer put fixing the
evidence before other line-A work, as part of Saturday's 23:00 ART checkpoint. All arms failed
their criteria; the diagnosis showed that arms 3 and 4 measured missing evidence, not a model
choice. The scope covers methods not probed, probe failures that lose the real reason, stored
rows cut at 8, one classified example per function, and `compute_price` finding no difference
in 48 inputs.

The bench rule counting every unclassified difference as unavailable stays unchanged. If that
rule ever changes, the report must say it changed after seeing results. The order is one run
with the scripted fake model to confirm the evidence changed, its numbers to the maintainer,
then one paid run: one measurement at a time.

Reason: arms 3 and 4 measured missing evidence, not a model choice; one measurement at a time.

## 2026-10-30 — B1: Omitted optional additions compare shared parameters

At 23:31 ART on October 29, the maintainer decided that when the candidate adds an optional
parameter and an input omits it, that input is compared over the parameters both versions
share. A changed default of an existing parameter, explicit argument values that differ, and
the two repeats stay strict. Public counts include only inputs with an identity.

The contract's description of `compared_count` is to change accordingly; the schema shape and
`CONTRACT_VERSION` stay unchanged. This work belongs to `changes/input-identity`.

## 2026-10-30 — B2: Defaults may resolve a uniquely bound module literal

At 23:31 ART on October 29, the maintainer decided that a default naming a module constant
resolves when that constant is bound exactly once in the same module to a literal, read from
the AST. No expression is ever evaluated. This narrows the opaque-default rule in
"2026-10-29 — Behavior inputs follow unique module aliases and require opaque arguments".
This work belongs to `changes/input-identity`.

Reason: read from the AST; no expression is ever evaluated.

## 2026-10-30 — B3: Classify one witness per outcome group within a function

At 23:31 ART on October 29, the maintainer chose option a): within one function, group
reproducible differences by outcome kind: returns a different value, now raises X, no longer
raises X, or raises Y instead of X. Classify the smallest witness of each group and say on its
card how many differing inputs it covers. This work belongs to `changes/difference-groups`.

Raising the row and card limits was rejected because it requires a contract change, more
tokens and more cards. Classifying more witnesses within 8 was also rejected: functions with
25–26 differences still fail. This coverage rule was introduced after seeing the bench results,
and the bench report must say so.

Reason: one classified example per function leaves other reproducible differences unclassified;
group coverage addresses that evidence gap without raising the row and card limits.

## 2026-10-30 — B4: A different public object state gets an outcome card

At 00:00 ART on October 30, the maintainer chose option a): a method or setter whose result is
the same on both sides but leaves the object's public state different gets its own outcome
kind, "leaves the object in a different state", with a card like the other kinds. This work
belongs to `changes/methods-1`.

Reason: showing the difference only in rows, without a card, leaves it unclassified and counted
as unavailable; that alternative was rejected.

## 2026-10-30 — Only the level 2 replay test gets Playwright's slow allowance

Reason: real-time replay takes about 50-59 s on fast runners and 80-95 s on slow ones, leaving no margin against the 90 s timeout; only this test is marked slow.
The timeout in `playwright.config.ts` and `make e2e`'s 600 s cap are unchanged.

## 2026-10-30 — Bench method scope supersedes D9's level 3 deferral

The maintainer includes methods in the bench evidence fix scope. This supersedes
"2026-10-28 — D9: Behavior probes for methods wait for level 3"; that entry remains as the
record of the earlier decision.

Method probing is assigned to two fronts: `changes/methods-1` covers sync methods and
properties of simple project classes on receivers built from literal constructor calls,
comparing result and public state; `changes/methods-2` covers object arguments. The static
rejection of randomness and time access and the other effect filters stay unchanged. Only
classes defined in the project's own non-test modules are constructed.

Reason: deferring methods leaves missing evidence in the bench, so the evidence fix scope
supersedes the level 3 deferral while retaining the existing effect and construction policies.

## 2026-10-29 — Item 9's card and inbox: answers, content, shots and where grants are listed

The maintainer decided on 2026-10-29, as follow-up 3 to item 9 and the decisions inbox. Earlier
entries record the inbox itself ("A decisions inbox is the form of Needs you": No is Request
changes, Unsure stays in the inbox), the header count, and what becomes a question ("Item 9
questions are only for unasked or untied differences"). This entry records the rest: how the
card's answers gate its actions, what the card and the inbox show, the shots gate, and where
standing grants are listed.

Approve on a card with questions opens only when every answer is Yes, and the reason sits beside
the locked button: "Approve opens only when every answer is Yes". With any No, Request changes
becomes the primary action, prefilled with that question's example as the comment. With any
Unsure, the card waits. Reject is always available. In Ask me, every change still waits for
Approve.

The card says "N choices need you" and gives one line, from events, with what was asked and what
was done. Each question shows its smallest example as large before -> after cells, the after cell
in the agent's color, with Yes keep it, No send it back and Unsure decide later; an answered
question collapses to one line with its answer. A block headed with what was compared,
such as "No other difference in what was compared · 1 function, 24 inputs", counts the functions
and inputs compared, as one row of function squares per module, beside the changed module with its
changed functions numbered to their questions, and their callers; functions not compared stay
neutral, and the block never claims that everything else is the same.
A footer reads "k of N answered"
next to the locked Approve. The inbox shows the large count; one line computed from events, such
as how many agents took how many steps in how long and how many of those decisions needed a
person; when each decision arrived; and rows with their before -> after, never a row whose before
equals its after. The earlier prep mockups give content and hierarchy only. The screens use the
product's own components at the quality of the v4 shell sketches, with data from a real recorded
run (the demo, or Boltons) that names only files that exist.

Before any card or inbox front merges, the maintainer sees shots of the card's three states and of
the inbox, in dark and day. Once he approves them, they go into docs/sketches/ beside the v4
sketches.

Standing grants get no Settings tab for grants alone. The grants list sits next to the inbox, as a
second view of the same panel or a section under the decisions, with its own count. The inbox
answers front shows that placement in its first shots; a reason it does not work comes back with
them.

This keeps every decision the person makes, and every answer they gave once for good, in one
place, and Approve, which merges into main, never opens while an answer still says No or Unsure.

## 2026-10-29 — Review shows bounded direct behavior evidence

Review renders the latest facts matching both revision and head, without falling back to older
optional evidence. Direct changed-node probes remain separate from measured caller observations
and the static “possibly affected” cone. All published nodes survive, including removed or unknown
IDs; graph labels resolve where available and IDs remain the fallback.

Counts describe admitted inputs and stable measured differences. No same-result count is displayed:
ten comparisons and nine differences can include instability beyond the eight retained samples.
Visible instability is partial evidence, never an established difference. Positive comparisons with
zero differences say “No difference measured on these inputs” only without published limitations or
visible instability; the bounded-input qualification remains beside it. Null or omitted evidence is
not recorded, an empty list means no changed-node probes, and facts completeness says nothing about
behavior completeness. Missing current facts stay explicit in review; the portable block itself
returns null without direct evidence so other parents can choose their own absent-state copy.

Published reasons are limited to unsupported node kind, unavailable version and unavailable probe.
Only a graph-confirmed method/class gets the specific method/class wording. Unknown reasons are
preserved as text; no timeout, isolation or signature diagnosis is inferred. Samples are escaped,
losslessly wrapped display strings, with disclosures initially closed to preserve editor space.

DESIGN.md wins over Impeccable's ordinary 16px body floor and generic card/motion recommendations:
review keeps its existing 14px dense evidence role, theme tokens, plain surfaces and event-driven
focus rules. Facts, signals and the agent report share one bounded context scroll even without
a summary, so expanded evidence cannot overlap Monaco or the decision footer at 1280×800.
No new animation or visual world is introduced.

## 2026-10-29 — Measured caller evidence stays inside static possibilities

Review shows published caller outcomes only for the current proposal revision and head, joined
by node id to the retained static cone in distance order. A missing name falls back to its id.
Observed equality means “No difference measured on these inputs”; it establishes neither
behavioral equivalence nor coverage. A caller without measurement stays possible. Invocation
counts remain independent of caller counts and bounded display samples; unstable samples never
supply stable difference evidence.

Availability is independent of outcome: partial evidence preserves stable differences, while
zero admitted comparisons shows unavailable measurements and the published reasons. Matching,
recording and probing diagnostics remain visible, without inferred causes or use of overall
facts completeness as caller completeness. Missing/null legacy evidence differs from an explicit
empty array. The static graph, bounds, omissions and possible callers remain visible beside the
focus panel, and both summary paths share one context scroll without displacing the diff or
choices. Samples are escaped text, disclosed only by a person; incoming events never move focus.

Impeccable 4.5.2 conflicts resolved in favor of Signal: retain the project's dense title/data
roles instead of a generic 16px body floor or 65–75ch minimum, and add no authored motion to
measured evidence. Existing tokens, spacing, Next/Mono roles and review geometry govern.

## 2026-10-30 — Review panel rework: the approved sketches replace the fronts' review rules

The maintainer approved the review sketches on 2026-10-30, before 10:39 ART (exact time not
recorded). The baseline is [docs/sketches/review/](docs/sketches/review/README.md), not the final
look. It replaces the review rules added by behavior-review and measured-cone-review: "unmatched
stay visible", "Mono carries counts", and the mid-dot section. The critique ranks harm to the
approve/reject decision: grids and duplicate lists bury differences; diagnostic counters, equal
chips and mixed-width prose compete with the evidence. Differences lead, agreement is a sentence,
method details open on request, and the graph owns places and reach. Data contracts stay intact.

The maintainer's five settled departures, verbatim:

- the diff comes before "What was compared";
- "Out of view" covers functions off camera and inside folded modules;
- "varied" is the cone word for unstable results;
- the answered row always wraps, never truncates (at 1280 the After cell may need a scroll);
- state g's Revert is the item 8 target, built only when item 8 delivers it.

The approved baseline bends Signal's frame dimming to 16% outside the review cone and change,
including module frames. Grain and top light stay on the header. The sketch's local day syntax
values must become token-driven in the code step; day syntax tokens already exist, and this step
adds no colors. Mono's narrower prose rule applies only inside review: the agent card and dock
keep their numeric readings. Approval from the review panel starts merge travel like any merge;
with the agents panel folded, the pulse leaves that agent's letter in the rail. It never moves
the camera or takes focus from the panel. Revert remains absent until item 8 supplies its command.

Impeccable 4.5.2 shape ordinarily asks for brief confirmation and stops; this screen's approved
brief and explicit instruction to write DESIGN.md settle that step. Clarify's preference for
specific actions does not rename the approved Yes/No/Unsure answers or rewrite server questions.
The project's fixed sizes, tokens, matte header and focus rules win over generic style guidance;
AGENTS.md and DESIGN.md remain authoritative. No new visual world or UI code is introduced here.

## 2026-10-30 — Review panel life pass: a candidate after level 3

Not built now. Once level 3 is solid, a candidate life pass may add the answer fold transition,
a rolling answered count, and hover on a differing caller that lights its node. It goes through
the same sketch approval as this rework. These are later candidates; merge travel on Approve is
already required by the approved baseline.

## 2026-10-31 — Review words use the approved table and recorded rule results

No server phrase reaches the review panel verbatim. The table in `evidenceWords.ts` translates
reasons, and none says "probe"; an unknown phrase shows no reason. In g, the first three recorded
passes in the approved order make one sentence. "{n} more rules passed." follows when more
passed ("1 more rule passed." for one). "Your grant covered {k} rule(s) that could not be checked."
follows when a grant covered an unknown rule. Details lists each as "{name}: passed" or
"{name}: covered by your grant". Approved as implemented at `60152b2` in packet B: "Approve all",
before 2026-10-31T04:05:38Z (01:05:38 ART).

Reason: the panel uses the approved review words, and recorded passes and grant uses explain the
merge without exposing server diagnostics or inferring evidence from the current policy.

## 2026-10-31 — Map view and folder groups follow the later line B work

The map view and folder groups are left out of the current level. They come after the Conversation tab, milestone pulses and life layer part 2, built only from a sketch the maintainer approves (maintainer, 2026-10-31).
The depth control stays built (`bfe7a18`). The definition remains for a later level; sketch first.
Packet B's "Approve all", before 2026-10-31T04:05:38Z (01:05:38 ART), covered leaving these out
of the current level. The maintainer later placed them in this line B order on 2026-10-31.

Reason: the current level keeps its built depth control; the later map view and folder groups
require the maintainer's approved sketch after the preceding line B work.

## 2026-10-31 — Phone practice reviews with the panel's evidence

The phone practice uses the panel's evidence from f and f-b. The Oct 27 recording stays, so its
two decisions show kind d; a, b, c and d2 appear only in fixture tests. The maintainer said
"No, keep Oct 27", before 2026-10-31T04:14:42Z (01:14:42 ART). The progress row ("Decision {i} of {n}")
and the 44px "Recorded task" row with the exact task stay as departures: the practice holds two
recorded decisions, and their 40–60 word tasks need more than the headline. The "Possibly affected"
disclosure is removed: the graph owns the list (DESIGN.md, graph-owned review context), and the
fourth chip keeps the count.

Reason: the practice retains the real recording while sharing the panel's evidence order; progress
and the verbatim task remain available without duplicating the graph's list.

## 2026-10-30 — Item 10 measures agent-hours from observed intervals, with dated list prices

The maintainer amended item 10 of docs/additions.md. Agent-hours, project active hours and parallelism come
first, because proof 2's closing line uses them. They come from each agent's own working intervals in its
status events, not from fixed time buckets. Costs are list-price equivalents from Meta's official pricing
page: the repository stores the extracted price table with the page's URL and retrieval date, not a copy of
the page. Muse Spark Open via Baseten has no listed price, so its work is reported as a lower bound. Missing
usage, activity or price shows the known part as a lower bound and the total as unavailable.

Reason: Oversight records when each agent works, so measured intervals replace an approximation, and a waiting
lead must not inflate the hours. A stored price table needs no contract change; recording a price in
run.started was the rejected alternative. The coverage, grouping and comparison rules come from prior
work in the maintainer's projects, rewritten for Oversight; no code was copied.
## Contracts 1.8.0 — approved by the maintainer, 2026-10-30

E6 adds server-owned model.attempt_failed, once per failed attempt before its wait, including the
final failure. It records agent_id (the author for review calls), optional change_id and role,
model profile, provider, 1-based attempt and policy max_attempts, cause, HTTP status, provider
Retry-After, wait_s and final reason. Attempt cannot exceed the limit; attempts_used requires the
limit. Final is set exactly when wait_s is null. Rate limits require 429, server errors 500–599,
and refused a different status; connection and timeout require null status. AgentStep.error_kind
is optional, default null, and names only unknown_tool, invalid_json or invalid_arguments with
result error; other tool errors remain null. The card derives "Waiting for model (retry n/5)"
until that agent's next step, usage tick or failed attempt. There is no producer in this front;
E6's client and loop front owns emission. History envelopes remain unchanged.

P1–P2 replace fixed pairs with lists of one to six subtasks in Plan and UpdatePlan. A single
subtask is the one-agent fallback. Dependency indices are stable within each revision (no
reordering), range from 0 to 5, must be below the actual count, unique and exclude self. Both
models share a complete acyclicity check, including disconnected cycles.

P3 adds optional subtask state, null for historical payloads only. A plan cannot mix null and
non-null states; all-null states are allowed only for exactly two subtasks, retaining their
historical assignment rules. Plans with one or three to six subtasks require states. Waiting
has no agent; running and merged require one, with distinct per-start agent ids across subtasks
even when a slot is reused. A running plan requires an assigned agent, following from P4's rule
that a running queue item has at least one agent. Prerequisites must be merged for a running,
merged or rejected subtask: Reject, discard and stop reject a subtask that started. Dependents
of rejected or cancelled prerequisites are cancelled. Rejecting a plan cancels every subtask;
completion requires merged, rejected or cancelled subtasks. Request changes keeps a subtask
running.

P4–P5 make plan queue agent_ids the growing list assigned so far (one to six at dispatch and
completion); standalone dispatch still requires exactly one. A plan reserves only immediately
ready subtasks' slots. Newly ready dependents take the next released slot before later queue
items, preserving the plan's place. Only the person queues and dispatches an exact revision;
Plan.started_by records a requesting lead without dispatch authority, and plan agents keep
started_by null. PlanZone is unchanged. This reverses Contracts 1.6.0's "widening slots does not
enlarge a plan" and changes FIFO decision 6's "a plan reserves both slots together".

Safe creation, nullable SaveFile.base_hash, zones and inventory move again to 1.9.0 because E6
and item 11's plans take 1.8.0. SaveFile remains unchanged. Earlier entries remain approval
history. Generation and drift checks retain the pinned tools and 1.5.0 tuple baseline; bounded
lists generate ordinary arrays. Consumer changes are mechanical; E6 producers, item 11a's
scheduler and the card front follow separately.

## 2026-10-30 — Proof 2's tasks and reporting rules are fixed before the run

Tasks were approved at 09:32 ART and reporting rules at 10:10 ART on 2026-10-30.
P1-P6 were approved by 11:57 ART; the [plan](docs/plans/proof-2.md) fixes the rules before the run.
D1: 16 Boltons tasks, 12 issue-backed and four tests-only; parallel pairs 1-2 through 13-14, task 15 after 5,
task 16 alone. D2: seven feature APIs and examples are frozen in tasks.json. D3: tests-only changes pass on
base, kill at least two of three frozen mutants and change no production code. D4: open upstream PR tasks
stay, pinned at d31d821; checks follow issues, not PRs; replace a task only if its issue is resolved before
the run; agents have no network. E1: reviewer verdicts against hidden checks, all four cells, unknowns apart,
and review cost per change. E2: hidden checks on every reviewed revision; cited fixes use P2. New regressions
have two counts: passing at task base then failing now, and passing at the previous reviewed revision then
failing now. E3: unasked behavior changes split by difference kind; Boltons method tasks await methods-1.
E4 causes: checks_unavailable = missing evidence; review_card = model judgment; risky_area,
examples_before_code, lead_plan and follow_main = the person's policy, never the other two causes. Policy
questions are by design; for follow_main, a conflict or a failed combined test always comes to the person.
follow_main also has its own parallel-agent cost subcount; assign new decision kinds a cause before the run.
E5: paired-arm cost and wall time per correct change, by role, including failed/rejected work; hours and
parallelism. Meta's official pricing page is the source; the repo stores the extracted table with the page URL
and retrieval date, not a page copy; one leak-guard exception applies. Baseten is a lower bound. E6: per
model/host, 429/capacity errors per agent-hour, retries, retry halts and malformed calls, from contracts
1.8.0. E7: compare finish files/tests_run/tests with server diff/tests; omitted claims count apart; never
model-parse prose. Under 10 cases, counts only, never a percentage. Proof 2's US$25 cap includes the uncounted
pilots. H1: same-model harness-pack pair runs within the bench's US$20 only after both free passes have at
most 4/20 question-not-available cases each, equal counts, and every case labelled unasked_difference whose
tests pass in both passes flips between them. Bring the numbers before paying. L1: rework/re-splits/conflicts,
zone overlaps, round-0 acceptance against tasks the person started; L2: planning spend. L3 is future work.
With the lead on main, measure L1-L2; otherwise, if hidden checks, item 8, E6 and item 10 are on main, the
person queues the 16 tasks at once; starts count as their decisions and L1-L2 are not measured.
P1: correct change; P2: fixed what was cited; P3: questions that were not needed; P4: review cost per change.
P5: pilots 16 and 4 first, results excluded; the run then queues all 16, those two included; pilot costs
remain within US$25. P6: queuing keeps the execution constraints; per-check results and revision ids kept;
questions keyed by decision identity; agent-hours from elapsed run intervals; parallelism as the
concurrent-agent count over time, wall time apart. Reason: freeze tasks, definitions and reporting before
seeing results; later rules are marked added after run start.
## 2026-10-30 — Merge authority: the person's approval or rules they set

The maintainer set PRODUCT.md's success sentence, which item 8 of docs/additions.md required before rules mode
can be enabled: "A change reaches main only with your approval or by meeting rules you set; its review then
shows the rules and grants that let it in, and Revert undoes it in one step when it applies cleanly." It
replaces "nothing reaches main without their decision" in Product Purpose, and the first product principle
now names the same two authorities. README.md keeps "Nothing reaches main without the person's approval"
while rules mode stays closed by default, and changes when the mode can be enabled.

Reason: rules mode merges a change without a separate approval, so a product statement that requires the
person's decision for every merge would contradict it. Naming both authorities, with a review that shows what
let the change in and a Revert that undoes it, keeps the person in control.

## 2026-10-30 — Order after contracts 1.8.0, and when plans split

The maintainer set the order of the next fronts:

- Contracts 1.8.0 first, with two plan passes in parallel: item 11 (queue, dispatcher and one-subtask plans
  first; completeness, partition and targets second) and the lead.
- Then 11a (queue, dispatcher and plan service with one-subtask plans) and the producer of
  `model.attempt_failed`.
- Then the lead, which depends on 11a and item 8, not on 11b or the plan view.
- 11b (completeness evidence and the partition) runs only if a free measurement on Boltons and the demo
  repository, under the completeness rule below, shows that splits actually happen; otherwise it waits until
  after proof 2. The plan view follows 11a and does not block proof 2.
- While the partitioner has two zones, plans use one or two subtasks; six is for the lead.
- Item 8 continues with the merge mode's isolated start capability, closed by default today, and the
  merge-mode routes.
- No other contract change before proof 2 unless a front cannot work without it.

Completeness is conservative. The graph counts as complete only if every Python file the graph scan finds
was parsed, with none skipped for size, syntax or encoding. The scan's own exclusions (hidden directories,
`__pycache__`, symbolic links, a module shadowed by its package) are not gaps. An unresolved possible call
between two zones counts as a conflict, and any doubt falls back to one agent. The partitioner may stay at two
zones first.

Reason: one-subtask plans and the queue give the lead a path to proof 2 that does not wait on splitting, and
splitting is built only if it happens on the repositories proof 2 uses. A wrong split costs a conflict the
person has to resolve, so doubt falls back to one agent.

## 2026-10-30 — Browser tests use plain ground unless they test the field

All 24 Chromium launch sites under frontend/src use browserLaunch.support.ts. WebGL is disabled
by default through Chromium itself: a new canvas returns null for getContext("webgl2"). This selects
the product's existing no-WebGL path: the field canvas is hidden, its state is plain, and draws is
zero. No product timer, motion setting, renderer or theme changes. Review's browser test asserts
all four observations without a context shim; the lamp test explicitly asserts WebGL2 availability.

Only app/dotField.browser.test.ts (lamps, contours, travel, context loss and look),
graph/field/mask.browser.test.ts (rendered masks and markers), and
graph/field/cost.browser.test.ts (field buffer captures and frame cost) request webGL: true.
Other captures concern UI controls or graph geometry, not the field's appearance. No current e2e
spec tests the field, so e2e uses the same default options helper. A field spec can explicitly use
browserLaunchOptions({ webGL: true }); OVERSIGHT_E2E_SOFTWARE_RENDERING=1 still selects SwiftShader.
A direct browser probe with that variable checked both the disabled and enabled modes: exit 0.

Reason: on the reported ubuntu-26.04 runner (4 vCPU, software WebGL), the life layer's 500 ms
repaints compute the full height field and draw the lamp grid throughout unrelated graph tests.
Reported CI comparison, run 40988845881 at af1a874 versus 40989304590 at 8fad4c2, seconds:

| File | Before life layer | After life layer |
| --- | ---: | ---: |
| review | 53.4 | 87.0 |
| reviewControls | 51.6 | 93.3 |
| palette | 59.3 | 92.6 |
| shell | 59.2 | 87.5 |
| fileView | 33.6 | 52.0 |
| filesTree | 29.3 | 47.7 |
| functionTask | 62.6 | 105.6 |
| camera (already refuses WebGL2) | 8.5 | 8.5 |
| diffViewport | 34.5 | 34.5 |
| LiveDiff | 19.3 | 19.9 |
| folds | 6.9 | 7.0 |

The reported suite total was 393 s before the merge and reached its 600 s cap afterward;
level2 also reached its 90 s test timeout. Neither cap was raised; workers and retries are unchanged.

Local wall seconds on ad5de55 before any edit, then on this change, each invocation alone under
taskset -c 0-3, in order review, reviewControls, camera, repeated twice, then level2 once:

| File | Base run 1 / run 2 | Changed run 1 / run 2 | Exit codes, all invocations |
| --- | ---: | ---: | ---: |
| src/app/review.browser.test.ts | 40.88 / 36.14 | 23.12 / 28.71 | 0 |
| src/app/reviewControls.browser.test.ts | 36.58 / 33.88 | 21.98 / 35.46 | 0 |
| src/app/camera.browser.test.ts | 6.53 / 5.68 | 7.03 / 9.15 | 0 |
| e2e/level2.spec.ts | 76.78 | 69.45 | 0 |

Commands, unpiped; each browser file above was substituted for FILE and invoked twice per revision:

```sh
/usr/bin/time -f 'wall=%e exit=%x' taskset -c 0-3 pnpm -C frontend exec vitest run --config vite.config.ts --project browser FILE
/usr/bin/time -f 'wall=%e exit=%x' taskset -c 0-3 env UV_OFFLINE=1 OVERSIGHT_E2E_SOFTWARE_RENDERING=1 bash frontend/e2e/run.sh level2.spec.ts
```

The e2e wall time includes the launcher's two static builds and server startup on both revisions;
Playwright reported the changed scenario itself at 58.6 s. These local runs did not reproduce the
CI timeouts. The second reviewControls run did not improve, and camera varied despite already
avoiding WebGL: these are paired local observations, not a claim about the runner's final suite time.

Validation: the unpiped two-file command below exited 0 (33 passed; the two existing opt-in visual
captures remain conditional, unchanged). tsc --noEmit and eslint --max-warnings 0 on every changed
frontend file exited 0. No Python files changed, so ruff, pyright and pytest have no touched target.
The unpiped vitest related --run --maxWorkers 2 command over all 27 changed frontend paths
exited 0: 33 affected files, 202 passed and three existing conditional captures unexecuted.

```sh
pnpm -C frontend exec vitest run --config vite.config.ts --project browser src/app/review.browser.test.ts src/app/dotField.browser.test.ts
```

The existing long camera and dotField files and the native-shortcut test callback retain their
structure; this front changes only launch wiring and the two proof assertions. This decision log
remains append-only.

### 2026-10-30 — Browser launcher review corrections

The full-page captures in app/shell/agentLook.browser.test.ts intentionally show the product's
no-WebGL ground while checking agent cards, rail avatars, review pills and pending borders. They
do not claim to capture the field's appearance. The three field files listed above retain WebGL.

The dotField fixture loads the shared launcher inside beforeAll, so fixture collection needs no
new dependency stub. app/dotFieldChecks.test.ts is restored to its pre-front contents; its original
collection assertions reject an eager launcher import. The no-WebGL proof is a separate review
test that asserts the field canvas exists before reading its state. SwiftShader flags are now
added only when WebGL is enabled, with launcher tests covering the environment variable, explicit
overrides and preservation of other launch options. The cost test's header precedes its imports.

## 2026-10-30 — Item 11a: planner usage, start refusals, restarts and queue order

The maintainer accepted five answers for item 11's plan:

- The usage of a plan call the person requested, made before any agent exists, goes into `usage.tick` under a
  planner id local to the run, taken from the agent counter. That id never gets an `agent.status`, so the reducer
  records its cost without a card. No contract change.
- Before a plan's first start, its queue item follows the table for standalone tasks: a busy slot leaves it
  `queued` with a detail; the spending brake, a suspect main or missing configuration leave it `blocked`; any other
  refusal or a failed start marks it `failed`, the plan fails, and its `waiting` subtasks become `cancelled`. After
  a start, which only 11b or the lead allows, a refusal that may clear leaves the subtask `waiting` and the item
  `starting` or `running` with a detail. This follows the queue contract, where a `starting` item holds at least
  one agent and a `blocked` item holds none.
- Unimportable `.py` files, which the graph scan drops today, count as completeness gaps, and the split
  measurement reports them separately. They are not among the scan's own exclusions.
- The queue lasts for one run. After a restart the new run starts with an empty queue, and the shutdown `error`
  lists what never ran. There is no resumption across runs before proof 2.
- The queue is strict FIFO, skipping only blocked items. Queueing proof 2's 16 tasks from the UI is a separate
  frontend front.

Reason: each answer keeps 11a within contracts 1.8.0 and the rules standalone tasks already follow, which is what
proof 2 needs. Resuming across runs and queueing from the UI are not on proof 2's path.

## 2026-10-30 — The lead requests plans only, ends when done, and claims no independence

The maintainer accepted five answers for the lead's plan:

- A lead plan's independent subtasks are not zone-checked against each other, as the 16 standalone queued tasks
  are not. The person's one approval of the exact plan revision lets them run in parallel; conflicts and failed
  combined tests reach the person through follow-main, and nothing claims the subtasks are independent. This
  stays after 11b, whose zone checks cover only the splits the server proposes from POST /plans. The rejected
  options were one subtask per plan until 11b, and several subtasks only as a dependency chain.
- The lead does not queue standalone tasks before proof 2, only plans. Decision 9 of the 2026-10-27 batch (lead
  standalone queueing) waits until after proof 2.
- The lead never waits or replans: it ends at `done`. Waiting would need a new status, after proof 2.
- The lead's prompt is part of the prompt set, so `run.started`'s `prompt_version` covers it. Its limits are fixed
  in code, stated in its starting detail, and recorded in DECISIONS.md when the lead lands. No contract change.
- A rejected lead plan is final. The person may start a new lead with the rejection comment.

Reason: not zone-checking a lead plan's subtasks takes the same risk proof 2 already accepts with 16 standalone
tasks and claims nothing; it is the only option where the lead saves approvals while agents run in parallel.

## 2026-10-30 — 11b runs only if splits are common enough

The free split measurement decides whether 11b (completeness evidence and the partition) is built before proof 2.
11b runs if at least 1 in 5 Boltons task pairs splits with calls plus imports, and at least 1 demo repository pair
splits. Otherwise it waits until after proof 2, as the order entry above says. No measurement had been reported
when the rule was set.

The method is fixed with the rule. Each task's targets are the innermost functions and methods whose base-side
range contains a line the reference patch changes. New definitions map to nothing; a task with only additions
falls back to its containing node, and the report shows the denominators with and without that fallback. Pairs:
120 from proof 2's tasks on Boltons, 28 from the demo repository and 1,000 random pairs with a fixed seed, each
under two variants, calls only and calls plus imports. A pair splits only if each zone holds the targets of exactly
one task. An incomplete repository gives "no split possible" for all its pairs.

Result, on 2026-10-30 at 22:17 UTC (main e8a52e1, Boltons d31d821, seed 20261030): both graphs were complete. On
Boltons, calls plus imports split 15 of 21 eligible task pairs without the fallback and 26 of 66 with it; the other
pairs include a task with no targets. A random sample of 1,000 Boltons pairs split 779 times. On the demo
repository none of the 28 pairs split, under either variant or mapping; the partitioner's refusals were 17 for
coupled targets and 11 for too few groups. The demo condition fails, so 11b waits until after proof 2.

Reason: a rule set before the numbers keeps 11b from being justified by the results it is meant to test, and a
split that rarely happens does not pay for the conflicts a wrong split costs.

## 2026-10-30 — Item 8: a closed activation latch, and rules mode refuses without cross-review

The maintainer accepted six answers for item 8's plan:

1. The activation latch is a constant in code that only the activation front opens, not an environment setting,
   so configuration cannot open it in an unapproved build.
2. Losing isolation does not switch the mode to Ask me. Only the person switches; Oversight suspends rules
   authority and says why in one `error` event.
3. PUT `merge_when_safe` does not require a browser Origin header: proof runs use the command line. The lead is
   fenced by its route allowlist instead.
4. A disable keeps the revision check (compare-and-set). Every disable suspends authority and ends every wait at
   once; a stale disable answers 409, and the UI re-reads the mode.
5. The `run.started` seq identifies the protected-path table version in force, and the table's version is recorded
   in DECISIONS.md when protected paths land. No contract change.
6. Rules mode refuses while cross-review is off; cross-review stays optional under Ask me.

Reason: until activation, PUT `merge_when_safe` always answers 409 with the latch's reason, so the person's
authority and PRODUCT.md's merge-authority sentence stay true while the pieces land. Each answer fails closed
without a contract change.

## 2026-10-30 — Messages, pause and resume: 202 answers, three limits and memory-only text

The maintainer accepted seven answers for level 3's messages, pause and resume:

1. POST /agents/{id}/message, /pause and /resume answer an empty 202 once built, instead of 501.
2. Only time actually spent paused is excluded from the agent's wall-clock limit; time with a pause pending still
   counts.
3. Resume cancels a pending pause.
4. A message holds at most 4,096 characters.
5. At most 32 messages wait in an agent's queue.
6. An agent accepts at most 8,192 UTF-8 bytes of message text over its lifetime, never replenished.
7. Message text lives only in memory: no event carries it, so a replay shows a message's effects but not its text.

Reason: the limits bound the context messages add, since every step resends the full history, and memory-only text
needs no contract change.

## 2026-10-30 — No paid H1 pair: the free passes miss its third condition

H1, in proof 2's entry, allows the paid harness-pack pair only after both free passes meet three conditions. On
main ad5de55, with the fake model and no cost, the two asking arms met the first two: 4 of 20 cases with no
question available in each, and equal counts. They missed the third: b04 and b19, labelled `unasked_difference`
with tests passing in both passes, do not flip. b04's unasked difference appends a line to a file, and b19's falls
back to `uuid4()` when `sku` is empty; a probe that compares return values and receiver state sees neither. The
paid-run gate refused, and the maintainer chose not to run the paid pair. The bench's counting rules are
unchanged.

Reason: H1 was fixed before the run as the condition for paying. Meeting it would need probes that record file
effects, a new product capability, or a rule changed after seeing results.

## 2026-10-31 — Item 8 F4a: protected-path built-in table version 1

`safe-merge-paths-v1` uses built-in table version 1: `BUILTIN_CATEGORIES` in
`backend/src/oversight/changes/_protected_paths.py` as of this commit, identified in code by
`BUILTIN_TABLE_VERSION = 1`. Any change to the table needs a new version. The `run.started` seq
identifies the table version in force; the `change.proposed` seq separately witnesses the complete
proposal inventory. Neither receipt is reconstructed when unavailable.

Reason: F4a captures the sources without changing authority. Live enforcement lands atomically in
F4b; these helpers have no production callers, and F1's activation latch stays closed.

## 2026-10-31 — Item 8 F4b: protected paths join rules authority atomically

Pending reservations, definitive authorization and Revert preflight now require only
`safe-merge-paths-v1`, with exactly nine unique rules. There is no probes-profile fallback,
including for historical Revert evidence. The built-in patterns remain table version 1.
A directory pattern ending in `/**` also protects its bare directory entry: `.github`
matches the CI category, including when the entry is a symlink or Gitlink. This fixes the
boundary interpretation without adding or changing the table's patterns.

The confirmed proposal captures the original table and inventory using the owning
`run.started` and `change.proposed` seqs. Capture is independent of spend eligibility: a run
without a positive budget still identifies its table, but supplies no passing spend receipt.
Every use checks the retained committed proposal, exact change/agent/revision/base/head,
and both original source receipts. Missing, evicted, malformed or incomplete sources remain
unknown until the original evidence deadline produces one normalized Needs-you error;
they are never reconstructed as an empty inventory. A complete protected hit is an
independent fail and leaves the change ready for the person.

The existing runtime review remains independent of the rules wait. Rules eligibility already
requires cross-review to be enabled with an independent configured reviewer; invalidating
a path-vetoed wait does not cancel or prevent that review. A clean review cannot erase the
hit. The proposal author is matched by profile id; before, it never matched.
Unavailable, halted or cancelled review cannot grant permission. Ask me keeps its
existing opt-in review behavior. Author summaries and reviewer text cannot change this
table, waive a hit, or manufacture a grant. Protected-path grant requests remain refused,
including on a personal Approve; people retain the existing direct decision commands.

Prepared candidates remeasure name/status inventory from immutable Git objects against
the captured table, including both rename sides, binary paths and Gitlinks. Proposal diffs
ignore submodules, so their inventory alone is insufficient: workspace `integrity.veto_index`
already refuses Gitlinks before proposal commits and suite capture, and candidate inventory
explicitly includes them or reports incomplete. Candidate path results join all nine rules
in the committed definitive assessment before any commit. Hits or unknowns stop the
transaction and verified recovery restores main. Failed, timed-out or truncated candidate
queries instead stop with one normalized terminal notice; complete but unsupported entries
retain the persisted unknown veto. Persistence requires all three candidate measurements and
the protected-path receipt for that exact candidate tree. Source receipts remain required at every
barrier; the durable-spend reads and separately pinned spend evidence retain F3b's behavior.
Revert additionally requires zero protected violations and a complete observation.

The run's table receipt is pinned outside the bounded event cache for the whole run, separately
from spend provenance; ordinary event eviction cannot invalidate it. Startup forwards that
receipt even when the durable ledger is unavailable. Only protected-path evidence can use the
table pin; all spend evidence still goes through F3b's unchanged admission predicate. Missing or
invalid table pins and missing or evicted proposal receipts fail closed without reconstruction.

F1's production activation latch remains closed. These checks add deterministic authority
constraints; they do not enable rules merges, expand Revert to old probes-profile merges,
or add filesystem monitoring or a sandbox.
## 2026-10-31 — Start with the whole graph when it fits legibly; keep graph controls clear of the floating agents panel

The maintainer approved starting with the whole graph when its Fit zoom is between the shared
**Opening a change** legibility floor and 100%. The floor comes from `changeZoom()`, keeping node
names and state words at least 11 CSS px. A graph that fits at 100% or more keeps the centered 100%
start and gutter rule. A graph that cannot fit at the floor starts at the floor on the hub's module,
inside the graph bounds and moved to the nearest gutter to keep module frames whole.

The maintainer also approved reserving the floating expanded agents panel's width for graph
overlays and camera framing at 1440px and below: both end 24px from its edge. Docked panels,
folded rails and focus tabs keep their existing geometry.

Reason: the recorded demo at 1920×1080 fits at 83%, yet its 100% start left two of six modules
off-canvas. At 1280–1440px the floating agents panel covered the detail and zoom controls and
framed modules underneath itself; in the 1280×800 capture, models.py was cut at its x = 880 edge.

## 2026-10-31 — Revert admits a server-loaded committed rules resolution

The person-only HTTP route loads the exact SQL row from this run at or below the
acknowledged horizon. Missing sequences, other event types and other changes return 404;
unverifiable rules merges, unavailable isolation, suspect main and duplicate or closing
attempts return 409. Admission reserves the target synchronously before answering an empty
202; settlement reports only through events. Shutdown cancels Reverts in flight and awaits
their bounded settlement before the event store closes.

Reason: a client cannot supply authority through an event body or an unacknowledged row.
Immediate refusals keep 202 meaningful, while cancellation lets the tested inverse recovery
path settle a suite whose 60-second bound exceeds the service's 30-second shutdown budget.
## 2026-10-31 — Item 10 agent-hours come from a fold over the committed log

Reason: pure activity accounting reads only status, review, lead, step and usage-attribution
observations, never costs. A review interval starts at the author's latest ready_for_review,
or after the previous review of that change, whichever comes later. Open intervals are clipped
at the committed prefix's last event. Lead and plan work leave the total unavailable rather
than estimating their hours. Team grouping and hour/day periods come later, together with the
team definition.
## 2026-10-31 — Item 10 list prices: a stored dated table applied to each call's tokens

Reason: the stored table records its source and retrieval date; only Muse Spark has a listed
price. The fold applies those prices to each call's tokens and never reads the ledger's cost
fields. An unreported count gives a range whose lower end enters the known subtotal. Unpriced
work keeps the total unavailable, while zero usage costs zero even without a listed price.
Team grouping and periods come later.

## 2026-10-31 — Message text persists; privacy at export

This entry replaces answer 7 of “2026-10-30 — Messages, pause and resume”. The maintainer's
updated decision: “Message text must persist like the rest of the run, as a real product does;
privacy is handled at export, not by dropping history.”

- Accepted messages produce an `agent.message` event in the run's event log before delivery.
- Credential-shaped text and configured provider key fragments are masked before writing;
  the agent receives the original message.
- Local event files, including WAL and SHM, are owner-only (0600); the runs directory is 0700.
- Publication exports withhold message text by default; `--include-message-text` explicitly
  includes the already masked text, and the provenance receipt records that choice.

Not now: compression or blob storage. After proof 2's run, the event log's size and reopen time
will decide whether to propose content-addressed blobs and zstd for closed segments.

The reserved safe creation bundle (nullable `SaveFile.base_hash`, zones and inventory) moves from
1.9.0 to 1.11.0, superseding its earlier reservations; 1.10.0 is approved for
probe-inputs parameter-extension evidence.
## 2026-10-31 — Supervision is a pure fold over the committed event prefix

Reason: the maintainer needs reproducible supervision counts and spend for any recorded run
and replay, using existing receipts alone. The read-only summary has no decision authority.

The prefix starts at seq 1 and has no gaps, duplicates or reordered events. `fold_usage`
validates it and the costs first; supervision refuses an invalid prefix rather than showing
plausible partial totals. No event producer or contract changes are needed.

A change belongs to the agent in its first proposal or resolution, falling back to its first
attributed usage tick if neither exists anywhere in the prefix. When a proposal or resolution
exists, ticks do not establish or contradict ownership: lead and review calls may name a
different agent. Disagreement among proposals and resolutions, or among ticks when neither
receipt exists, refuses the summary with the offending seq. A change referenced only by an
update, status or error has no owner or calls and is omitted. Changes keep first-appearance
order, including those earlier references. Agents appear in agent-id order if they own a
change, have a step or have unassigned usage; status and focus alone do not introduce an agent.

Changes count distinct ids proposed at least once; proposals count every proposal event.
Outcomes count every resolution separately: approved/merged, approved/conflict, request changes,
rejected without a reason, or rejected/stopped. A change's end state uses its last resolution
after its last proposal. No such resolution, a request for changes, or a new proposal after a
resolution leaves it open. Open counts include owned changes with ticks but no proposal, while
the distinct proposed-change count excludes them; open counts can therefore exceed changes.
This preserves the brief's end-state definition and keeps unproposed work visible.
Conflict is terminal, not open. Review rounds count confirmed requests for changes, starting
at zero.

Lines proposed are two raw columns from each change's last proposal, summed by owner and run.
They are null on a change never proposed, which contributes no proposed lines to the totals.
Net lines merged sum added minus removed from the matching revision for every merged receipt.
A missing matching proposal makes that change's net and its owner's and the run's net unknown.
Steps count `agent.step` receipts and sum their recorded duration_ms, never wall-clock intervals.

All dollar figures come from `fold_usage`: owned `ChangeUsage`, its `RoleUsage` buckets, and
unassigned entries. Agent and run totals use `math.fsum`, include unassigned calls, and sum
unknown-cost call counts. Any null component leaves its dollar sum null, including unknown
bucket role or model even when every call's cost is known. Unknown dollars stay unknown; the
CLI prints six decimal places only for known sums. Decisions count person, rules, cross_review,
lead_agent and null (unrecorded), without inferring the missing actor in older logs.

Spend that did not reach main has four separate figures, each with change count, dollars and
unknown-cost call count. Rejected, stopped and conflict use the full usage of changes ending
in that state. Earlier rounds use only buckets below the number of requests for changes before
the final merge, only for changes ending merged; its count includes only changes with at least
one such bucket. A null included cost nulls that figure. Empty groups are zero. These figures
are never added together; open changes and unassigned calls belong to none of them.

The fold stores counts, not rates. The CLI states merged proposed changes at prefix end as
“N of M changes merged”, with M the distinct proposed changes; resolution counts remain separate.
Review time is refused because proposal-to-resolution elapsed time mixes waiting with work.
Rankings and scores are refused because agents receive different tasks. Percentages and ratios
such as lines per dollar or cost per merge are refused because these receipts provide no fair
normalization for task scope; every printed rate retains its explicit denominator.

## 2026-10-31 — Settings shows the merge mode only

The maintainer accepted the recommended answers at 12:47 ART. A refused mode change shows
one inline line below the cards, with an X icon, the server's words and role="alert";
no toast. Ask me's second sentence is dropped. Merge alone when safe reads
"A change merges by itself only when every rule holds. Otherwise it waits for you."
The rules checklist waits for a backed source. No left navigation, repository
label, Saved or scope line; Merge mode fills the tab. Both cards disable in flight,
with no words. Replay cards are read-only, mark the recorded mode, and show
"Setting the merge mode is not available in a replay." A null mode marks neither card
and omits the status segment. The cards act only on their own pointer click;
Enter and Space send nothing, with no shortcut, palette command or confirmation.
Item 8's accepted plan asks for a mode toggle that shows the server's 409 reason. Until rules
mode is activated, the live Merge alone when safe choice presents that refusal.

## 2026-10-31 — Revert in the merged review footer

Approved in packet B: "Todo como recomendás", at
2026-10-31T15:47:21Z (12:47:21 ART).

The merged review footer offers Revert for a rules merge with its recorded resolution and
main commit. In flight, the button disables and a line reads "Reverting: waiting for the result."
A refusal appears below the footer row, with an X icon, the server's words and role="alert".
After a Revert, the button goes away and a second line reads "Reverted in main {revert_commit}",
using seven characters, under "Merged into main {hash}". The header pill stays "Merged".
A Revert that needs you shows its reason verbatim as a muted line; Revert remains available.
In a replay, "Revert is not available in a replay." takes the button's place.
Revert acts only on its own pointer click. It stays focusable; Enter and Space send nothing.
There is no shortcut, palette command or confirmation. Availability does not read the merge mode.
Outcomes update the current view without opening a tab, moving focus or selecting a node.
