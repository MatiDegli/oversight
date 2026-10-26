# Frontend transport

Use this folder through `index.ts`. It is the only frontend feature that uses `fetch` and
`EventSource`. Each started transport returns `stop()` and `ready`: live initialization finishes
after connecting the stream; replay finishes after applying the whole log. Call `stop()` when the
owning shell unmounts, including during React StrictMode's development cleanup.

## Live synchronization

`GET /graph` supplies an authoritative graph checkpoint and its run id. The checkpoint's `seq`
does **not** mean that the client has applied the agent and change events before it. Proposal 4.6
takes precedence over the transport brief: a fresh tab opens `/events?after=0&run=...` and rebuilds
the complete event state from `run.started`. An existing session resumes after its last applied
event. `lastSeq` and the shell's `data-seq` always describe applied events, not a fetched checkpoint.

A sequence gap or graph-version mismatch closes the damaged connection, fetches a checkpoint,
and resumes after the last applied event. Graph events covered by the checkpoint apply that graph
as a snapshot through the reducer, while agent/change events still replay in full. This repairs a
version mismatch without skipping missing agent state or applying old deltas to a newer graph.
Events beyond the checkpoint use the normal reducer. A different run resets all old state.

Normal network reconnection stays with the browser's EventSource and its `Last-Event-ID` header.
The transport replaces the source only when recovering inconsistent state or changing runs.

## Replay and commands

`startReplay` accepts `{ text }` or `{ url }`. Speed 0 runs immediately; otherwise each timestamp
gap is divided by speed and capped at 2 seconds, as in the backend player. Replay uses the same
event application function as live transport, and rejects sequence/version inconsistencies.

`pnpm -C frontend exec vite build --mode replay` chooses bundled replay at build time and makes no
backend requests. The current bundle is explicitly labeled as a hand-written fixture. Replace it
with a real recorded run and update the label before publishing the judges' demo.

`createCommandClient` exposes every JSON command in proposal 4.7. Accepted commands return no
result; SSE provides their results. HTTP errors use `CommandError` with `status`, contract `code`
and server `message`. Network errors keep their original browser error.
