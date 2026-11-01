# Instructions for agents

Oversight is built for the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026). The maintainer is the
architect: they own the contracts and make the critical decisions. Only the coordinating agent merges, once the checks
pass and a reviewer from another provider has no open blocking or should-fix finding; for frontend views and docs, a
should-fix off the demo path becomes a follow-up front (docs/proposal.md, section 7).

Repository content is in English. Talk to the maintainer in Spanish, with times in ART (UTC-3).

Read for your task: PRODUCT.md (what and for whom), ARCHITECTURE.md (modules, contracts, graph rules, agent tools),
DESIGN.md (every visual rule), DECISIONS.md (each decision with its reason; it wins over older text), docs/additions.md
(inventory and status).

## Hard rules
- All project code is new, written in this repo since the kickoff. No code from the maintainer's other projects; never
  mention or link them. Keep README.md's "Built during the hackathon" section and one DEVLOG.md line per day.
- Product agents use only Meta models: Muse Spark through the Meta Model API (Contributor mode) and Muse Spark Open
  through Baseten.
- Meta's API is called only by Oversight itself and by runs started on purpose (the API probe, recording a demo run).
  Tests and make check use the scripted fake model and spend no credits.
- Development agents may use Muse Code with its own stored credential: never the product's .env key, never exported
  in the shell, never the hackathon credits.
- API keys live only in .env (.env and .env.* are gitignored; *.example files are kept). gitleaks runs in pre-commit
  and CI. Hooks always run: never --no-verify.
- Dependencies are MIT, Apache-2.0, BSD or ISC; fonts may be SIL OFL 1.1. Never copy code from GPL projects.
- contracts/ is the source of truth (Pydantic, with generated JSON Schema and TypeScript checked byte for byte in CI).
  Never change it; propose the change to the maintainer.
- One module per agent, one worktree and branch per module, small PRs with tests. Never git stash or rebase; merge
  main to follow it.
- A change that leaves a file over 500 lines or a function over 80 says why in its hand-back; the reviewer decides.

## Architecture invariants
- Git through its CLI, never GitPython. Model calls through the official OpenAI SDK at the provider's endpoint, behind
  the small client interface; no LiteLLM. Every git command and test run is an asyncio subprocess with a timeout; never
  a blocking subprocess.run.
- Events: append-only SQLite (WAL, busy_timeout, one writer task, strictly increasing seq), streamed over SSE at
  /events with seq as the id and Last-Event-ID resume. Never one event per token: batch model text and writes.
- One lock for git operations on main and one per worktree. Approve merges into main; when main moved since the
  agent's base, the tests run on the combined result first.
- Model calls: retries with exponential backoff and jitter on 429 and 5xx; the reasoning mode is set on every call;
  missing token usage is unknown, never zero; the spending brake refuses new runs above 90% of the credits.
- Frontend: Cytoscape.js and fcose without a React wrapper; Monaco from the local package, never a CDN; one pure event
  reducer shared by live runs and replay. The static build plays a bundled recorded run, always labeled as real.
- The product's agents work in worktrees of .runtime/workspace/ (a fresh copy of demo-repo/), never in worktrees of
  this repository.

## Product agent safety
- Tools are list_files, read_file, search, edit_file, run_tests and finish. There is no shell tool.
- Every path is validated inside the agent's worktree; no tool returns the content of an obvious secret file.
- run_tests runs one fixed command, with a timeout, in the demo repo's own virtual environment.
- An agent's status comes from its tool calls, never from its text. Runs without progress stop.

## UI invariants
- One graph, always visible in the Oversight view; Chat loads it on the first switch. The panels change with what the person does. Never steal focus.
- Approve has no keyboard shortcut, not even Ctrl+Enter.
- A dashed node border means only a pending change, in its agent's color.
- Color is an agent or a state, never alone and never amber; every color comes from a theme token.
- Design work follows DESIGN.md with Impeccable 4.5.2 (allowed: shape, harden, onboard, audit, critique, polish,
  clarify, typeset, layout). Where Impeccable clashes with DESIGN.md or this file, ours wins and DECISIONS.md records it.

## Quality
ruff, pyright and pytest; tsc strict, eslint, prettier and vitest; import-linter; recorded events validated against
the schema; Playwright over replay. make check runs them all; CI runs them on every push.

## Environment
WSL on the Linux filesystem; the UI is opened and recorded in the Windows browser.
