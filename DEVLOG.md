# Devlog

One line per day, in Argentina time (ART).

- **2026-10-26:** Kickoff at 13:00 ART; repository setup, license, secret scanning, tooling and CI;
  proposal and design docs, per-front plans; demo repository; contracts v1 and 1.1 with schema and
  TypeScript generation and drift checks; SQLite event store, SSE and event player; graph parser,
  call resolution, span index, deltas, impact cone and preset layout; workspace and worktree runner;
  Meta API probe and Contributor default; agent runtime with the scripted fake model, failure
  limits and revision runs; change manager hooks (suspect-main check and a draining shutdown);
  API wiring, spending brake and shutdown; frontend theme, event reducer, store, live transport and
  fixture replay; graph view,
  agent card, watch mode and live diff; review mode with Approve and Reject and the impact cone;
  task box and Start agent; spend meter; layout and transport fixes; smoke test, screenshot tooling,
  test memory and time caps, parallel check groups and pytest workers; `make demo` and
  `make demo-check`.

- **2026-10-27:** Extended `make demo-check` to the level 2 API path with two paced fake agents,
  correlated SSE waits, revision/merge, Stop/discard, main file edits and usage; review saving is
  explicitly skipped pending its parallel front.
