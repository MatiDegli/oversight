# Frontend transport

Use this folder through `index.ts`. It is the only frontend feature that uses `fetch` and
`EventSource`. Each started transport returns `stop()` and `ready`: live initialization finishes
after connecting the stream; legacy replay finishes after applying the whole log. The controlled
player is ready once the recording has loaded and validated. Call `stop()` when the
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
error is reported per failure streak; the shell clears it on the next applied event. A lost
connection is also cleared when the reopened stream opens (`onError(null)`), because a server with
nothing new to send applies no event. A bad event is not cleared that way: the reopened stream
replays it. Persistent bad events remain visible and retry with bounded waits instead of skipping
or rewriting history.
Disposal cancels pending retries and ignores late callbacks.

## Replay and commands

`startReplay` accepts `{ text }` or `{ url }`. Speed 0 runs immediately; otherwise each timestamp
gap is divided by speed and capped at 2 seconds, as in the backend player. Replay uses the same
event application function as live transport, and rejects sequence/version inconsistencies.

`startAppTransport` dynamically loads `replayPlayer.ts` only for browser replay. Its subscribable
snapshot and play/pause/seek/restart/rate/dispose operations use a fixed 1× axis from
`replayTimeline.ts`: first event at zero, then cumulative gaps clamped to 0–2000ms **before**
applying 1×/2×/4×. Duration and milestones therefore stay fixed. Pause and rate changes preserve
partial gaps; seeking publishes one validated prefix from the initial state. Seek preserves
playing/paused state, except backward from ended pauses. Restart and Play again play from zero
at the selected rate. Disposal revokes loading and timer publication. The session owns this
player; its view never starts another one. Backend SSE replay remains live transport here.

`pnpm -C frontend exec vite build --mode replay` chooses bundled replay at build time and makes no
backend requests. The current bundle plays the real recorded run in
`fixtures/events/recorded-demo.jsonl`. Its status-line label reads the provenance date
(`Recorded run · Oct 27, 2026` for the current export). A missing recording or
receipt fails the public build; the e2e and scan seams select their fixtures explicitly.

`createCommandClient` exposes the commands the UI uses: 16 methods in `commands.ts`, including
reads. It does not cover every reserved or implemented backend route. Accepted commands return no
result; SSE provides their results. HTTP errors use `CommandError` with `status`, contract `code`
and server `message`. A body with a `message` but a code the contract does not name keeps the
message and has `code: undefined`. Non-JSON or malformed error bodies fall back to an HTTP status
message and `code: undefined`, without inventing a contract ErrorCode. Network errors keep their original
browser error.
