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
