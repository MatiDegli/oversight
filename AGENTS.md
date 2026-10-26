# Instructions for agents

You work on Oversight, a project for the Meta Global AI Developer Hackathon (2026-10-26 to
2026-11-04). The maintainer is the architect and the reviewer: they own the contracts and approve
every merge.

Repository content is in English. Talk to the maintainer in Spanish. State times in Argentina time
(ART, UTC-3).

## Product

A visual IDE for Python projects, for supervising coding agents. The repository is a live graph of
modules, classes, and functions parsed from the source. Agents on Meta's Muse Spark edit the code,
each in its own git worktree. The nodes an agent reads or edits light up in its color; clicking a
node opens its live diff. When a function changes, the graph shows its impact cone, every direct and
indirect caller, as "possibly affected". The developer approves, rejects, or requests changes.

The graph is for the human's supervision, not for the agents' retrieval. Do not claim that it helps
agents find code.

## Hard rules

- **No product code before the proposal is approved.** First write `docs/proposal.md` with the
  directory tree and the contracts, then wait for the maintainer's approval.
- **All code is new,** written in this repository during the hackathon. Never copy code from other
  projects, and never mention or link other private projects.
- **Secrets.** Agents inside the product use only Meta models through the Meta Model API. API keys
  live only in `.env`, which is gitignored. Never print, log, or commit a key.
- **Licenses.** Dependencies must be MIT, Apache-2.0, BSD, or ISC; fonts may be SIL OFL 1.1. GPL
  projects (Zed, pyan) are for ideas only: never copy their code.
- **Contracts.** The models in `contracts/` are the source of truth. Never change them; propose the
  change to the maintainer, who bumps the version and regenerates both sides.
- **Work in small pieces.** One module per agent, one worktree and branch per module, small pull
  requests with tests.

## Agreed design

- **Web first.** A Tauri desktop shell is level 3 at most.
- **Transport.** Server to UI: events over SSE, resumed with `GET /events?since={seq}`. UI to server:
  commands over REST. Every event carries `seq`, `ts`, `type`, and `payload`.
- **Contracts.** Pydantic models are the source. JSON Schema and TypeScript types are generated from
  them, never edited by hand, and CI fails on any difference. A backend test validates every event
  in the recorded logs against its schema.
- **Store.** An append-only SQLite event store is the source of truth; the live stream is a view of
  it. It also feeds replay mode.
- **Graph.** Python `ast`. Extraction per file is cached by content hash; resolution reruns in full
  after every change. Node ids are qualified names, never line numbers. Edges are `exact` or
  `possible`, never a score. Calls in comprehensions, lambdas, and nested functions belong to the
  nearest enclosing function. The impact cone is a reverse breadth-first search over `exact` call
  edges, bounded by depth and node count, and it reports what it cut.
- **Agents.** Our own tool loop on the official OpenAI SDK against the Meta endpoint, without
  LiteLLM. Tools: `list_files`, `read_file`, `search`, `edit_file`, `run_tests`, `finish`. No shell.
  An agent's status comes from its tool calls, never from its own text. Usage the API does not report
  is recorded as unknown, never as zero. A scripted fake provider runs the backend without credits.
- **Changes.** No file watcher: every edit enters through the API and emits its event. git through
  the CLI. Approve merges, bound to the agent's base commit and the hash of the result; reject
  discards the worktree's change; request changes sends the comment back to the agent.
- **Frontend.** React, TypeScript, Vite, and pnpm. Cytoscape.js with fcose, without a React wrapper.
  Graph positions are computed once, stored in `graph.snapshot`, and reused as a `preset` layout.
  Monaco through `@monaco-editor/react`, from the local package, never a CDN; diffs always use
  Monaco's DiffEditor. One pure reducer applies events for both the live stream and replay; state
  lives in Zustand.
- **Look.** Dark everywhere, Monaco included. Color is an agent (A orange, B blue) or a state (green,
  red), nothing else, and never alone: letters A and B, + and − in diffs, icons on tests. Dashed means
  "not in main yet"; possible calls are thin and grey. Type: Atkinson Hyperlegible Next and Mono,
  from Fontsource packages.

## Still open

- Whether change events carry the full original and modified text (Monaco's DiffEditor needs both).
- The zoom levels for large repositories.
- The review header's material.

Do not settle these in code; raise them in the proposal.

## Scope levels

- **Level 1, by Thursday 2026-10-29:** the graph, Monaco, one agent in its worktree, live node
  highlighting, a read-only live diff panel, approve or reject with a graph update.
- **Level 2, by Sunday 2026-11-01:** two agents in parallel with their colors, the impact cone,
  request changes, user edits that update the graph, stop and discard.
- **Level 3, only if levels 1 and 2 are solid:** messages to a running agent, pause and resume,
  cone overlap alerts, a replay timeline, a usage panel, `possible` call edges drawn on the graph.

A level is done when its demo runs three times in a row from a clean start, with one command and no
manual fixes. Do not start a level before the previous one is done.

## Quality

- Backend: ruff for lint and format, pyright, pytest. Python 3.12 with uv.
- Frontend: tsc in strict mode, eslint, prettier, vitest.
- End to end: one Playwright test on replay mode.
- Hooks: pre-commit with gitleaks (`uvx pre-commit install`).
- Commands: `make dev`, `make check`, `make demo`.
