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
- **The frontend's lint is type-aware** (`strictTypeChecked`), with the exhaustive-switch rule on,
  so a new event type fails the build until every switch handles it.

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
