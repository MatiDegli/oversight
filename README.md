# Oversight

Agents write code faster than anyone can review it. Oversight is how you stop reading code without
losing control.

Today, in a small web IDE for Python projects, you watch coding agents built on Meta's Muse Spark
work on a live graph of your repository. Each agent has its own git worktree. The functions an
agent reads or edits light up in its color, and each change shows its impact cone, the functions
possibly affected. Nothing reaches main until you approve it.

Try [Would you approve it?](https://matidegli.github.io/oversight/): a real recorded run in your browser, no install, works at phone width; choose “Two-change practice”.

Video: (link added at submission)

The idea of running both versions of a change to find what tests miss is not new. Testora (ICSE
2026) checks behavior differences in pull requests against their intent. DiffTestGen (2026)
generates tests that expose them. Meta's just-in-time catching tests (FSE 2026) generate tests that
catch a bug before a change lands. CONTRA (2026) asks only the clarifying questions whose answers
change behavior, checked by running programs. Oversight brings these ideas to supervising agents
while they work. Product measurements come from Oversight's own bench; the build-process figures
below are labeled separately.

**Status:** in development during the Meta Global AI Developer Hackathon, from 2026-10-26 to
2026-11-04.

## What works today

- On by default: a live graph of the repository and an impact cone of functions possibly affected.
- On by default: agents work in their own git worktrees, with reads and edits lit on the graph.
- On by default: live diff and review with Approve, Reject and Request changes.
- On by default: four agent slots, configurable up to six (A–F), working in parallel.
- On by default: a spend meter and a brake that refuses new runs above 90% of the credit budget.
- The static replay build is on by default and plays the real two-agent run in
  `fixtures/events/recorded-demo.jsonl` without a backend. Its
  label comes from the recording's provenance sidecar: `Recorded run · Oct 27, 2026`.
- On by default: facts and a summary derived from events for each change (item 1).
- Built, off by default: an opt-in agent harness pack with syntax checks, bounded search and test feedback (item 3).
- On by default: checks that a new test fails before the change and passes after it (item 6).
- Built, off by default: opt-in cross-review and automatic Request changes (item 7).
- Built, off by default: opt-in behavior diff, comparing old and new functions on the same inputs, with graph count chips and sample popovers.
- Built, off by default: opt-in measured caller evidence in review and measured caller words on the review graph.
- On by default: live overlap on the graph and agent cards, including shared edits and earlier reads.
- Built, off by default: backend merge by rules, idle follow-main waits and a live Revert route; rules activation stays closed pending approval, and Revert UI remains in progress.
- In progress: a change card with three states.
- In progress: aggregate cost and rounds UI; archived change totals and event-derived usage measurements are built.
- In progress: server-computed plan zones and plan UI; plan routes, dependency dispatch and the task queue are built.
- On by default: standing grant selection, listing and revocation; automatic use stays behind closed rules activation.
- On by default: a lead runtime that requests plans for the person's approval; it never starts coding agents or merges changes.

Muse Spark through the Meta Model API writes the changes. In the bundled recorded run, agent B
wrote with Muse Spark Open through Baseten. Cross-review with Muse Spark Open is optional and off
by default (`OVERSIGHT_CROSS_REVIEW`), and absent from that recording. Nothing reaches main
without the person's decision while merge by rules stays closed.

## How it was built

Oversight was built during the hackathon with a hand-run version of its own workflow: parallel
coding agents, each on its own branch and copy of the repository; a reviewer from a different
model provider on every change; and a coordinator merging only after the full checks pass.

These figures describe **our build process, not Oversight measurements**. This dated snapshot
is not the current main count: Oct 29, 2026,
22:38 ART, on main `d768d6d`.

- 187 fronts (one task each) run through the loop; latest acceptance at review round 0: 128, round 1: 40, round 2: 5; median 1 review round per front.
- 416 full verification runs over 380 review rounds: about 1.1 full suites per round.
- Writer slots: 3 per coordinator line, two lines; at most 6 concurrent writers.
- 13 collisions between two fronts found only at landing, after both were accepted.
- 243 merges to main and 1,126 commits on main since the kickoff.
- Tests are 59% of tracked Python and TypeScript source lines, excluding generated contracts, fixtures and the demo repository.

## Built during the hackathon

- The hackathon runs from 2026-10-26 at 12:00 ET to 2026-11-04 at 21:00 ET (13:00 to
  23:00 ART). All code in this repository was written since the kickoff, and the git history shows
  it. The first commit came after the kickoff, and [DEVLOG.md](DEVLOG.md) records what was built
  each day.
- Before the kickoff there were planning notes and UI sketches, and no code. What the project takes
  from them is written again here, under `docs/`.
- No code comes from other projects. Dependencies keep their own licenses: MIT, Apache-2.0, BSD, or
  ISC, and SIL OFL 1.1 for fonts. GPL projects were read for ideas only.
- Agents inside the product use only Meta models: Muse Spark through the Meta Model API, and Muse
  Spark Open through partner inference.

### Recording a real run

To bundle a finished real run, install gitleaks 8.30.1 and run `make record RUN=<id>` (or
`make record` for the latest run). This reads the SQLite log without model calls or credits and
writes `fixtures/events/recorded-demo.jsonl` with a `.provenance.json` sidecar. Keep both files
together: the static replay build verifies the exporter marker, SHA-256, run id, real model
profiles and every event against the contracts, and scans for secrets again before bundling.
Fake runs, unmarked fixtures, invalid events, secrets and absolute paths are refused. The public
static build requires the recording and its receipt; UI tests select their own fixtures explicitly.

## Next

- Saved agent flows: after the lead, pick a repository flow with a target and expected checks, review or edit its filled task text, then start the agent.
- Hidden test sets: extend beyond the external hidden grading already present in `tools/eval`.
- A red agent to look for counterexamples to a proposed change.
- A specialist engine for checks that need a dedicated tool.
- Two independent attempts on one task: twice the generation cost; separate from the existing automatic revision round.
- Executable disagreement between the two models, resolved with an example.
- Canary changes: a product workflow beyond the seeded defects already in the task bench.
- Fork a run from a recorded step.
- Answer a review question from a phone.
- Text rules attached to areas of the repository.
- Tell the later agent about an overlap in its next tool result; each agent already has a worktree.
- Select tests from the graph inside the agent loop, starting in shadow mode.
- Per-test coverage, beyond the per-test outcomes already recorded.
- Mask older tool results on long runs; measure dollars and correctness before adoption.
- Generate inputs for behavior that the bounded probes did not reach.
- Run the bench on a real repository.
- Filesystem isolation for tests and probes (bubblewrap on Linux); today's isolation covers the
  network and the process tree only. No filesystem sandbox during the hackathon.
- After the hackathon, as a plan first: run the repository's git hooks as evidence, not inside
  commits: in the isolated runner on the proposal, shown on the card as passed or failed, with any
  formatter edits appearing in the diff. Agent commits keep hooks disabled.
- After the hackathon, as a plan first: repositories without Python open without a graph; agents,
  worktrees, diff, review, cross-review, merge and tests still work. Needs a per-repository test
  command with JUnit output and a center view without the graph (sketch first).
- After the hackathon, after the native Windows port: a Tauri shell, starting with one bounded spike
  in its own directory that touches no product code: a Windows window showing the current UI,
  starting the backend inside WSL, and passing a folder chosen with the native dialog (Windows path
  translated to WSL). Dependency licenses are reported first (MIT, Apache-2.0, BSD or ISC only).
- After the hackathon: keys typed into a packaged app's Settings, write-only and kept by the
  local backend; needs the secrets rule changed, a contract change and a reviewed security plan.

### Scale and depth

Depth and scale are two axes. The built **Modules / Functions / Details** control (`bfe7a18`)
sets how much unfolds. Scale is the zoom: how it is drawn.

The **map is not built**. It would appear automatically when module titles fall below the reading
floor, replacing each module with a circle at its block's center, with area proportional to its
function count. Folder groups appear on the map only and are not built either. The earlier middle
level, function dots inside module frames, is dropped.

See [Scale and depth in DESIGN.md](DESIGN.md#scale-and-depth) for the approved definition and scope:
the map and folder groups follow the Conversation tab, milestone pulses and life layer part 2,
with an approved sketch first. See the DECISIONS.md entry “2026-10-31 — Map view and folder groups
follow the later line B work”.

![Earlier sketch of zoom levels](docs/sketches/mockup-zoom-signal-far.png)

*Earlier sketch, not the target. Its structure comes from a sketch made before the kickoff,
redrawn in Oversight's look on 2026-10-26. Its name and numbers are placeholders; it includes the
middle level now dropped.*

See [docs/additions.md](docs/additions.md) for the queue and candidates.

## License

MIT. See [LICENSE](LICENSE).
