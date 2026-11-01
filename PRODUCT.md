# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **The supervising developer.** One person who hands tasks to coding agents on a Python repository
  and decides what reaches main. They want control: nothing merges without their decision or a rule
  they set, and every decision rests on facts they can check without reading the code, such as what
  a change does differently, how far it reaches on the graph, and what its tests prove. The graph
  exists for this person, not for the agents.
- **The power vibe coder.** Runs many agents at once and wants speed. They keep several agents
  working while only the decisions that matter reach them: a change that passes every rule merges
  by itself with its reasons and a Revert, and the rest arrives as a short question with an example.
- **Hackathon judges.** They evaluate Oversight through a 3-minute demo video and a static build
  that replays a real recorded run in the browser, with no backend and no setup.

## Product Purpose

Oversight is a small, focused web IDE for agentic development on Python projects. The repository is
shown as a live graph of modules, classes, functions and methods parsed from source. Coding agents
built on Meta's Muse Spark edit the code, each in its own git worktree. Success is a person who can
let two agents work in parallel and still know, at any moment, what each one is touching and what a
change may affect. A change reaches main only with your approval or by meeting rules you set; its
review then shows the rules and grants that let it in, and Revert undoes it in one step when it
applies cleanly.

Status, 2026-11-01: rules activation is open following the maintainer's approval. Each fresh run
starts in Ask me; the person explicitly selects Merge alone when safe. Selection requires session
isolation C and an independent cross-reviewer; every merge still requires all current evidence and
transaction guards. Clean rules merges offer person-triggered Revert. Behavior diff and measured
callers remain opt-in, off by default. Activation evidence uses scripted fake models; the public
static build continues to replay the separately labeled real recording.

## Positioning

Agents write code faster than anyone can review it. Oversight is how you stop reading code without
losing control. Both audiences get it from one mechanism. Oversight runs the old and the new version
of each changed function on the same inputs and keeps what behaves differently. It measures which
callers on the graph changed as a result. Then it asks the person only about the differences the
task did not ask for. Control comes from that: nothing merges without the person's rules or their
decision, and every question carries its smallest example. Speed comes from that too: several
agents run at once, and only the questions that matter reach the person. The IDE (the live graph,
the agents' lights, the diff) is where this happens, not the pitch.

## Operating Context

- Built during the Meta Global AI Developer Hackathon, Oct 26 to Nov 4, 2026.
- Developed in WSL; the UI is opened and recorded in a Windows browser on localhost.
- The demo runs against a small Python project of our own (`demo-repo/`), copied to a fresh runtime
  workspace on every start.
- Judges are meant to see the demo video and a static replay build. The build plays the bundled
  real two-agent recording, labeled from its provenance (see Evidence on Hand).

## Capabilities and Constraints

- Watch mode: the graph and the agents panel, with live node highlighting and agent cards.
- Review mode: an inline diff in Monaco beside the graph with the impact cone; decisions Approve,
  Reject and, from level 2, Request changes. Approve has no keyboard shortcut.
- Live diff (read-only) and file view share the review panel; there is no third screen.
- Agents use only Meta models: Muse Spark through the Meta Model API, and Muse Spark Open through
  Baseten. Their tools are fixed (list, read, search, edit, run tests, finish); no shell.
- The impact cone is bounded and always presented as "possibly affected", never as complete.
- Scope is set by levels in docs/proposal.md, section 5; level 3 items and day mode are cut if time runs short.
- Terminology: agent A and agent B, change, pending change, impact cone, watch, review, live diff,
  file view, approve, request changes, reject.

## Brand Commitments

- Name: Oversight.
- Language: English, in sentence case.
- Dark by default: the product, the video and the demo are dark. A day mode is for the submission
  gallery only.
- The visual system is recorded in DESIGN.md; AGENTS.md wins over both files.

## Evidence on Hand

- **Exists today (Oct 28):** the demo repository (`demo-repo/`); hand-written and scripted fixtures
  for UI tests; and the frontend with a graph, agent cards and Monaco review panels.
- The real two-agent run (`fixtures/events/recorded-demo.jsonl` and its provenance sidecar) records
  Muse Spark and Muse Spark Open, with both changes approved and merged. The static replay build
  plays it without a backend, with the provenance-derived label `Recorded run · Oct 27, 2026`.
- No users, testimonials, benchmarks or metrics exist yet; none may be invented.

## Product Principles

- The person decides: a change reaches main only with their approval or by meeting rules they set.
- Never steal focus: the screen changes only when the person acts.
- Show what may be affected, honestly bounded, rather than claim completeness.
- What judges see is real and labeled for what it is: a replay of a real recorded run says so;
  the hand-written log, if it is ever shown, says it is hand-written, never a recording.

## Accessibility & Inclusion

- Color is never the only signal: agents carry letters, diffs carry `+` and `-`, tests carry icons.
- The two agent colors are chosen to stay distinct with common color blindness.
- Text meets 4.5:1 contrast and meaningful graphics 3:1; keyboard focus is always visible; motion
  stops under `prefers-reduced-motion`.
