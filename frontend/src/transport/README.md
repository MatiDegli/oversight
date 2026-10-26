# Frontend transport

Use this folder through `index.ts`. It is the only frontend feature that uses `fetch` and
`EventSource`. Each started transport returns `stop()` and `ready`: live initialization finishes
after connecting the stream; replay finishes after applying the whole log. Call `stop()` when the
owning shell unmounts, including during React StrictMode's development cleanup.

## Live synchronization

Live connects directly to `/events?after=0`, applying the run from `seq` 1 through the same
pure reducer as replay. An existing session resumes with `after=<last applied>&run=<run id>`.
`GET /graph` remains available for debugging and graph-only clients; connecting never calls it.
Graph, agent and change state come exclusively from applied events, including a new run's reset.

A parse failure, reducer exception, sequence gap or graph-version mismatch closes the source and
retries from the last applied cursor. All network errors also close and retry, including a CLOSED
EventSource after a non-200 proxy response. The retry window doubles from 500 ms to 30 seconds,
with equal jitter (50–100% of the window). The first applied event resets the window. One visible
error is reported per failure streak; the shell clears it on the next applied event. Persistent
bad events remain visible and retry with bounded waits instead of skipping or rewriting history.
Disposal cancels pending retries and ignores late callbacks.

## Replay and commands

`startReplay` accepts `{ text }` or `{ url }`. Speed 0 runs immediately; otherwise each timestamp
gap is divided by speed and capped at 2 seconds, as in the backend player. Replay uses the same
event application function as live transport, and rejects sequence/version inconsistencies.

`pnpm -C frontend exec vite build --mode replay` chooses bundled replay at build time and makes no
backend requests. The current bundle is explicitly labeled as a hand-written fixture. Replace it
with a real recorded run and update the label before publishing the judges' demo.

`createCommandClient` exposes every JSON command in proposal 4.7. Accepted commands return no
result; SSE provides their results. HTTP errors use `CommandError` with `status`, contract `code`
and server `message`. Non-JSON or malformed error bodies fall back to an HTTP status message
and `code: undefined`, without inventing a contract ErrorCode. Network errors keep their original
browser error.
