# Graph cleanup evidence

Baseline: `97e739a` (`frontend/graph-sketch`). The comparison uses the proposed hand-written fixture,
dark theme, 1920×1080, a 1464×1028 graph canvas, pricing at 100%. No model API is involved.

| Measurement | Before | After |
| --- | ---: | ---: |
| Complete rows inside the start viewport | 31 | 15 |
| Call/inheritance edge bounds intersecting the start viewport | 32 | 24 |
| Rows in the production graph, including those offscreen | 40 | 23 |
| Call/inheritance edges in the production graph | 36 | 28 |

Rows mean childless nodes (functions, methods, folded classes and empty modules), excluding parent
frames. Edge visibility means an intersection with its rendered bounding box; an edge can extend
beyond the viewport. Imports remain hidden. The baseline's 50 nodes and 47 total edges include parent
frames and 11 imports, so those totals are not counts of visible rows or drawn calls.

`make shots` writes metrics beside each PNG. Baseline metrics were measured from an archive of
`97e739a` with the same fonts, viewport and proposed fixture, using the same read-only probe counters.
The three side-by-side PNGs are external artifacts in `/tmp/graph-clean/`. The requested research
report directory is mounted read-only in this clone; the coordinator can copy the pairs there.
Reference pixels and reference source are not included in this repository.

## Differences that remain

- The real demo still has 36 callables. Pricing has six rows and models has five; the sketch caps
  its modules at three rows and uses a different demo repository. Module folding is outside this front.
- The start view includes three of the demo's four call-depth columns. Orders remains offscreen;
  panning and Fit reveal it. The camera still starts on pricing at 100%.
- Same-module calls must leave right and arrive left, so they detour around their own frame. This
  makes them longer than the sketch's internal side loops. No unrelated frame is crossed.
- The live-diff fixture is Agent A editing pricing; the reference is Agent B editing inventory.
  Panel content and the dot-field remain owned by other fronts.
- If a person drags frames over one another so a gutter endpoint is enclosed, the edge is hidden
  until a clear route exists. Every demo call has a valid route, both fully open and fully folded.

## Verification and review signals

Pure tests cover click/edit/fold-again transitions, added methods, read projection, nearest cone
distance, deterministic coordinates, direction and shared trunks. Integration tests check every
demo call against unrelated module and class frames, open and folded. Browser tests exercise clicks,
edit-triggered opening, later folded-class rows, unchanged camera and other columns, and reduced motion.
Required hand-back gates: `make check`, `pnpm -C frontend test:browser`, `make shots`, `make e2e`,
`make demo-check`. Gate results are reported in the hand-back against the final commit.

`frontend/e2e/replay.spec.ts` is over 500 lines: it retains the existing sequential level-1 replay
walkthrough; this change only adjusts expectations for folded representatives. The long existing
`placeAdditions`, `GraphView`, `graphStyles`, `frameBox`, and preset `build` functions keep their
algorithms together; this front extracts placement without mixing a broader refactor into the
visual change. No new function is over 80 lines.
