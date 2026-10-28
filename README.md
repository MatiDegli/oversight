# Oversight

Agents write code faster than anyone can review it. Oversight is how you stop reading code without
losing control.

Today, in a small web IDE for Python projects, you watch coding agents built on Meta's Muse Spark
work on a live graph of your repository. Each agent has its own git worktree. The functions an
agent reads or edits light up in its color, and each change shows its impact cone, the functions
possibly affected. Nothing reaches main until you approve it.

Being built during the hackathon (see [docs/additions.md](docs/additions.md) for the state of
each): for every change, Oversight runs the old and the new version of each changed function on
the same inputs and shows what behaves differently. It also measures which callers changed, and it
asks you only about differences the task did not ask for. A change that passes your rules can merge
by itself, with its reasons and a Revert.

The idea of running both versions of a change to find what tests miss is not new. Testora (ICSE
2026) checks behavior differences in pull requests against their intent. DiffTestGen (2026)
generates tests that expose them. Meta's just-in-time catching tests (FSE 2026) generate tests that
catch a bug before a change lands. CONTRA (2026) asks only the clarifying questions whose answers
change behavior, checked by running programs. Oversight brings these ideas to supervising agents
while they work. Any number in this README about Oversight comes from its own bench.

**Status:** in development during the Meta Global AI Developer Hackathon, from 2026-10-26 to
2026-11-04.

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
- Merged on day 1: repository tooling and CI; contracts v1 and 1.1 with generated schemas and types;
  the demo repository; the SQLite event store and SSE; the graph core, impact cone and preset
  layout; the worktree runner, agent runtime and scripted fake model; the change manager; and API
  wiring with the spending brake. The UI includes the graph, agent cards, live diff, Approve and
  Reject, task entry, the review impact cone and the spend meter. The runtime also supports revision
  runs through `POST /changes/{id}/request-changes`, including the review UI.
- `make demo` builds and serves the frontend and API together; starting an agent uses API credits.
  `make demo-check` checks the API demo path three times from clean starts using two scripted fake
  agents, without API keys or credits: overlap, Request changes and a higher revision, merge,
  Stop/discard during work, a file edit on main, usage ticks and saving the editable new side
  (`PUT /files` with `agent_id`). Smoke tests, screenshot tooling and test resource caps are merged.
- Level 2 closed on Tue Oct 27 when `make demo-check` passed three runs in a row from a clean
  start using the scripted fake model (anyone can rerun it), and `make e2e` passed over the
  scripted two-agent replay.
- The static replay build plays the real two-agent run in `fixtures/events/recorded-demo.jsonl`.
  Its label, `Recorded run · Oct 27, 2026`, comes from the recording's provenance sidecar.

To bundle a finished real run, install gitleaks 8.30.1 and run `make record RUN=<id>` (or
`make record` for the latest run). This reads the SQLite log without model calls or credits and
writes `fixtures/events/recorded-demo.jsonl` with a `.provenance.json` sidecar. Keep both files
together: the static replay build verifies the exporter marker, SHA-256, run id, real model
profiles and every event against the contracts, and scans for secrets again before bundling.
Fake runs, unmarked fixtures, invalid events, secrets and absolute paths are refused. The public
static build requires the recording and its receipt; UI tests select their own fixtures explicitly.

## Next

- Lead agent: deferred; the former Sunday gate required packages 1–6, and the run rule now comes later; lead routes and plan `started_by` remain reserved even if the lead never ships.
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

### Zoom levels

For a repository too large to read at once, the graph would switch between three levels as you zoom:

- **Modules:** each module is a circle sized by its function count, with one line per pair of
  modules, thicker with more calls.
- **Functions:** modules become frames and functions become dots, named only where an agent works or
  the impact cone reaches.
- **Details:** the labeled boxes of the current view, with every call.

Agents and the impact cone stay visible at every level, nothing moves when the level changes, and
labels keep their size on screen. Not built yet; it comes after the core features, if time allows.

![Design sketch of the zoom levels, not built](docs/sketches/mockup-zoom-signal-far.png)

*Design sketch, not built. Its structure comes from a sketch made before the kickoff, redrawn in
Oversight's look on 2026-10-26. It is a picture of the idea, not the product: its name and numbers
are placeholders.*

See [docs/additions.md](docs/additions.md) for the queue and candidates.

## License

MIT. See [LICENSE](LICENSE).
