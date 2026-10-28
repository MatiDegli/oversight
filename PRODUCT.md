# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **The supervising developer.** One person who hands tasks to coding agents on a Python repository
  and decides what reaches main. They watch the agents work, read each change with its possible
  impact, and approve, request changes or reject. The graph exists for this person, not for the
  agents.
- **Hackathon judges.** They evaluate Oversight through a 3-minute demo video and a static build
  that replays one real recorded run in the browser, with no backend and no setup.

## Product Purpose

Oversight is a small, focused web IDE for agentic development on Python projects. The repository is
shown as a live graph of modules, classes, functions and methods parsed from source. Coding agents
built on Meta's Muse Spark edit the code, each in its own git worktree. Success is a person who can
let two agents work in parallel and still know, at any moment, what each one is touching, what a
change may affect, and that nothing reaches main without their decision.

## Positioning

The person supervises agents on a map of the code itself: the nodes an agent reads or edits light up
in its color, a change shows its impact cone (the functions possibly affected, directly or
indirectly), and every change waits for an explicit decision. The graph is the review surface, not
a chat transcript or a file list.

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
- Scope is set by levels in AGENTS.md; level 3 items and day mode are cut if time runs short.
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

- The person decides: nothing reaches main without an explicit approval.
- Never steal focus: the screen changes only when the person acts.
- Show what may be affected, honestly bounded, rather than claim completeness.
- What judges see is real and labeled for what it is: a replay of a real recorded run says so;
  the hand-written log, if it is ever shown, says it is hand-written, never a recording.

## Accessibility & Inclusion

- Color is never the only signal: agents carry letters, diffs carry `+` and `-`, tests carry icons.
- The two agent colors are chosen to stay distinct with common color blindness.
- Text meets 4.5:1 contrast and meaningful graphics 3:1; keyboard focus is always visible; motion
  stops under `prefers-reduced-motion`.
