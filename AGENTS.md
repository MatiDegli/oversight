# Instructions for agents

You are the lead engineer for a 9-day hackathon project (Meta Global AI Developer Hackathon, Oct 26 – Nov 4, 2026). I am the architect: I own the contracts and make the critical decisions. You approve merges once the checks pass and a reviewer from another provider has no open finding (docs/proposal.md, section 7).

Repository content is in English. Talk to me in Spanish. State times in Argentina time (ART, UTC-3).

## Product
A small, focused web IDE for agentic development, for Python projects. The repository is shown as a live graph of modules, classes, functions and methods parsed from source. Coding agents built on Meta's Muse Spark (Meta Model API) edit the code. Each agent appears on the graph as it works: the nodes it reads or edits light up in its color, and clicking a node opens its live diff in a Monaco editor. When a function changes, the graph shows its impact cone: the functions possibly affected, directly or indirectly. The developer approves, requests changes on, or rejects each change. The graph is for the human supervising the agents, not a tool for the agents.

## Hard rules
- No product code before I approve the proposal: first write docs/proposal.md with the directory tree and the contract schemas, then wait.
- All project code is new, written from the kickoff on, in this repo. No code from my other projects, and never mention or link them.
- The repo goes public before submission, and judges must be able to see what was built during the hackathon: keep a "Built during the hackathon" section in README.md and one line per day in DEVLOG.md.
- Agents inside the product use only Meta models: Muse Spark through the Meta Model API, in Standard mode, and Muse Spark Open through partner inference (Baseten).
- Meta's API is called only from Oversight itself: its agents, and runs started on purpose, such as the API probe or recording a demo run. Tests and make check use the scripted fake model and spend no credits.
- Secrets: API keys live only in .env. The repo's own .gitignore excludes .env and .env.*, keeping *.example files. gitleaks runs in pre-commit and in CI.
- Dependencies must be MIT, Apache-2.0, BSD or ISC; fonts may be SIL OFL 1.1. Never copy code from GPL projects (Zed, pyan): ideas only.
- The contracts in /contracts are the source of truth. Never change them; propose changes to me.
- One module per agent, one worktree and branch per module, small PRs with tests. No git stash and no rebase to follow main.

## Architecture
- backend/ (Python 3.12, uv, FastAPI):
  - graph core: ast parser to nodes and edges; stable node ids from qualified names; span index from file and line to the innermost node; re-parse of changed files after every edit, merge or discard (no file watcher), driven by a content hash per file; impact cone as a bounded reverse BFS over call edges.
  - agent runtime: tool loop on Muse Spark using the official OpenAI SDK pointed at Meta's endpoint (no LiteLLM), behind a small client interface so the provider format can be swapped. The model and endpoint are set per agent: agent B may run Muse Spark Open (meta-models/muse-spark-open-1026) through Baseten's Chat Completions API. One git worktree per agent.
  - event bus: an SSE stream at /events, backed by an append-only SQLite event store (stdlib sqlite3). Each event's SSE id is its seq; on reconnect the browser sends Last-Event-ID and the server resumes from there.
  - change manager: live diffs, proposals, approve = merge the agent's worktree into main, request changes = send my comment back to the agent and keep its worktree, reject = discard. Git through the CLI, no GitPython.
- frontend/ (React, TypeScript strict, Vite, pnpm):
  - graph view: Cytoscape.js and fcose used directly, no React wrapper. One component owns the instance and applies batched updates, keeping positions stable.
  - Monaco editor and DiffEditor, loaded from the local package (never the CDN), with its workers configured in Vite.
  - state: one pure function that applies events, shared by live runs and replay, stored in Zustand.
  - agents panel and review panel, styled with Tailwind CSS (Cytoscape and Monaco keep their own styling); icons from lucide-react.
  - replay mode: plays recorded event logs, so the UI can be built and tested without the backend or API credits. The frontend must also build as a static site that plays a bundled recorded run with no backend: that is the live product judges can open, always labeled as a real recorded run.
- One origin: in development Vite proxies API and SSE calls to FastAPI, with no compression or buffering on the SSE route; for the demo FastAPI serves the built frontend.
- contracts/: Pydantic models are the source. JSON Schema and TypeScript types (json-schema-to-typescript) are generated from them. TypeScript and the generator are pinned, and CI compares the regenerated types byte for byte.
- demo-repo/: a template of plain files in this repo, with no git of its own: a small Python project of our own (4-6 modules, 20-40 functions, tests) with one central function that has many callers.
- desktop/ (level 3, optional): a Tauri shell. The demo runs in the browser.

## UI
- One graph, always visible, with a side panel that changes with what I am doing. Dark theme everywhere.
- Watch mode: the graph and the agents panel. Each agent has a card with its status, task and steps. The nodes it reads or edits light up in its color.
- Review mode: on the left, a panel about half the width with Monaco's inline diff; from level 2 its new side is editable. On the right, the same graph with the impact cone highlighted and everything else dimmed. The agents panel folds into a narrow rail.
- The live diff (read-only) and the file view use that same panel. There is no third screen.
- Never steal focus. When an agent finishes, the screen does not change: a "Ready for review" pill appears and the agent's card shows it.
- Three decisions: Approve and Reject (level 1), Request changes (level 2). Approve has no keyboard shortcut, not even Ctrl+Enter: it merges into main and must never fire while I type a comment.
- A dashed node border means one thing only: a pending change, in the agent's color. "Possible" call edges are thinner and grey, never dashed.
- The demo repo has a saved preset layout, so the graph looks the same in every take and does not jump when it changes. On Tue Oct 27, try orthogonal ("taxi") edges and keep them if they read better than curves.
- The "no tests" badge (level 3) comes from coverage: run_tests runs with pytest-cov (MIT), and covered lines map to functions by their line ranges. If that is not ready, the badge reads "no test calls it" and is computed from the call graph.

## Look
- Signal identity: the graph is the machines' surface, the panels are the person's. Panels are dark and plain; Monaco uses a dark theme with low-chroma syntax colors. The review header is matte: flat dark with a still grain.
- Color is an agent (A orange #ff5c1f, B blue #4f7dff) or a state (green passed or added, red failed, removed or destructive), nothing else, and never alone: letters A and B, + and - in diffs, icons on tests. No amber warnings; counters across agents are neutral.
- Type: Atkinson Hyperlegible Next for text and graph labels, Atkinson Hyperlegible Mono for code, hashes and numbers, both from Fontsource packages; sentence case. Monaco is created after the fonts load, or calls monaco.editor.remeasureFonts().
- The dot-field light behind the graph is its own canvas under a transparent Cytoscape canvas. It comes after level 1; until then the background is plain black.

## Runtime workspace
- On start, the backend copies demo-repo/ to .runtime/workspace/ and runs git init there, so every demo starts clean.
- The product agents' worktrees live in .runtime/worktrees/{agent_id}, outside the parsed tree. .runtime/ is gitignored.
- Do not confuse the two kinds of worktree: my development agents use worktrees of this IDE repo; the product's agents use worktrees of the runtime workspace.

## Events (server to UI)
run.started (run id, exact model, prompt version, limits), graph.snapshot, graph.delta, agent.status, agent.step, agent.focus, change.updated, change.proposed, change.resolved, usage.tick, error. Every event carries seq, ts, type and payload.
- agent.status states include revising (after Request changes) and, in level 3, paused.
- agent.step: one per tool call, emitted when the call ends, with the step number, tool, short summary, result (ok or error) and duration. It feeds the agent card.
- change.updated carries the full original and modified file contents, plus its author (the agent or me): Monaco's DiffEditor needs both versions, and replay must not depend on worktrees that no longer exist.
- change.proposed carries the summary, files, nodes, lines added and removed, the test result, and the impact cone (node and distance).
- change.resolved carries the decision (approved, changes_requested or rejected), my comment, and the merge result (merged or conflict).
- On connect, the UI reads the snapshot and its seq together (GET /graph), then applies later events. If a seq is missing, it fetches a new snapshot.
- Never one event per token: model text is batched every few hundred milliseconds, and SQLite writes are batched.

## Commands (UI to server)
POST /agents, POST /agents/{id}/stop, POST /changes/{id}/approve, POST /changes/{id}/request-changes (with a comment), POST /changes/{id}/reject, GET /graph (snapshot plus its seq, read together), POST /graph/rescan, GET /impact, GET and PUT /files. PUT /files writes to main, or to an agent's worktree when the request names the agent (the editable new side in review). Events arrive only through the SSE stream at /events.
Level 3, in the contract from v1 and answering 501 until built: POST /agents/{id}/message, POST /agents/{id}/pause, POST /agents/{id}/resume.

## Graph rules
- A call is "exact" only when the name resolves to a single function; everything else is "possible", with its reason.
- Methods are nodes from day one.
- A function's range starts at its first decorator.
- Comprehensions, generator expressions, lambdas and nested functions are not nodes. Their calls belong to the nearest enclosing function or method, because it runs them or returns them. Names resolve in the nested scope first: a parameter, a comprehension target or a local assignment hides a module-level function of the same name. Exactness follows resolution only: a call that may not run, such as one inside an if, is still "exact" if its name binds to one function.
- Calls at module level belong to the module node; calls in a class body belong to the class node.
- Imports resolve: relative imports differ for a package __init__.py and a plain module, and import a.b binds the name a. Star imports, rebound names, attribute calls on unknown receivers, and methods a framework calls by name are "possible".
- Node ids never include line numbers. Two definitions with the same qualified name, such as a property getter and setter, get a suffix.

## Impact cone
- Bounded by depth and by node count. The UI shows what was cut, for example "+12 omitted by depth".
- Presented as "possibly affected", never as a complete list.

## Agent tools
- list_files, read_file, search, edit_file, run_tests, finish. There is no shell tool: a worktree does not isolate, since it shares .git/config and the stash.
- Every tool call emits agent.step when it ends. Read tools also emit agent.focus in read mode; edit tools emit agent.focus in edit mode and change.updated.
- read_file returns numbered lines and has a size cap; long files are read by line range. Prefer few, large reads of whole functions: every step resends the full history.
- edit_file fails unless the old text appears exactly once, or if the file changed since the agent last read it. The error goes back to the model.
- No tool can touch paths outside the agent's worktree: validate every path.
- run_tests runs one fixed command, with a timeout, in the demo repo's own virtual environment, never the backend's.
- Tool results carry no extra metadata.
- Stop a run that makes no progress: the same call with the same result four times, or six failures in a row.
- An agent's status comes from its tool calls, never from what the model says it is doing.

## Pending changes, approval and conflicts
- The graph is main's graph. Each agent's unapproved changes appear as nodes with a dashed border in its color, and their impact cone is computed on the agent's version.
- Each agent records its base commit. If main moved since then, for example after I approved the other agent, run the tests on the combined result before merging.
- If the merge conflicts or the tests fail, mark the change. I choose between asking the agent to redo it on the new base and rejecting it.
- Request changes discards nothing: the worktree stays as it is, the agent goes to revising, gets my comment as a new message and proposes the same change again.
- The new side of a diff is editable only in review, while the agent is idle. Those edits go to the agent's worktree, attributed to me. If I edited a file the agent had read, its next edit_file fails until it reads the file again.

## Robustness
- git commands and run_tests run as asyncio subprocesses with timeouts. Never a blocking subprocess.run: it freezes the event loop and the UI stops receiving events.
- SQLite in WAL mode with busy_timeout, and a single writer task for the event log, so seq is strictly increasing even with two agents writing.
- One lock around git operations on the main repo, and one per worktree.
- Retries with exponential backoff and jitter on 429 and 5xx from the API.
- Set the reasoning mode explicitly on every call. If the API does not report token usage, record it as unknown, never as zero.
- Spending brake: warn at 50% and 80% of the credits; above 90%, the server refuses new runs.

## Quality
- Backend: ruff (lint and format), pyright, pytest. Frontend: tsc strict, eslint, prettier, vitest.
- A scripted fake model that makes fixed tool calls, so the whole backend can be tested without API credits.
- A test that validates every recorded event against its schema: generated types validate nothing at runtime.
- import-linter in CI, so no module imports from another module's internals.
- One Playwright test over replay mode that checks the level's definition of done.
- pre-commit (gitleaks, ruff) and GitHub Actions running everything.
- Makefile: make dev, make check, make demo, make replay.

## Scope levels
- Level 1 (by Thu Oct 29): graph, Monaco, one Muse agent in its worktree, watch mode with live node highlighting and the agent card (status and steps), a read-only live diff panel, the "Ready for review" pill, review mode with Approve and Reject and a graph update.
- Level 2 (by Sun Nov 1): two agents in parallel with their colors (agent B on Muse Spark Open via Baseten if tool calling works, otherwise Muse Spark), impact cone (highlighted in review and listed in the panel), pending changes on the graph, Request changes, an editable new side in review, user edits from the file view that update the graph, a spend meter, stop and discard from the agent card.
- Level 3 (only if 1 and 2 are solid): cone overlap alerts, replay timeline, "Earlier today" history, messages to a running agent, pause and resume, starting an agent from a function, agent cursors with names in the editor, "no tests" badges, usage panel, dictating tasks with Muse Voice Transcribe, agent B running locally on my GPU (Muse Glimmer, Meta's other open model, is a candidate if it is smaller than Muse Spark Open), "possible" call edges drawn thinner and grey, inheritance edges, a Tauri desktop shell.
A level is done when its demo runs three times in a row from a clean start, with one command and no manual fixes.

## Out of scope for these 9 days
A persistent incremental engine, more languages, runtime traces, embeddings, a sandbox, undo, and a worktree pool.

## Environment
Develop in WSL, on the Linux filesystem. The UI is opened and recorded in the Windows browser, which shares localhost with WSL.

## Day-1 deliverables
1. Repo skeleton for the folders above, with the tooling and the Makefile.
2. contracts/ v1: Pydantic models, the generated JSON Schema and TypeScript types, and the CI drift check.
3. A fake event player that replays a hand-written event log over the SSE stream.
4. A smoke test that starts everything and checks the graph endpoint.
5. README.md (with a "Built during the hackathon" section), ARCHITECTURE.md (data flow and module contracts), DECISIONS.md (each decision with its reason) and DEVLOG.md (one line per day).

Before writing code, propose the directory tree and the contract schemas, then wait for my approval.
