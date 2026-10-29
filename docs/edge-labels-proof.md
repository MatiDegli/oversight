# Edge label and header proof (2026-10-29)

Baseline: `0c884f6`. Chromium, 1920×1080, dark, loaded Atkinson faces, reduced motion.
Boltons uses the pinned d31d821 snapshot and `fixtures/layouts/boltons-d31d821.json`;
demo uses the snapshot/preset from `fixtures/events/handwritten-level1.jsonl`.
The initial camera is 1.0; 0.2 zoom uses rendered pivot (800, 450), retaining node positions.

Each row counts distinct text labels with a positive-area viewport intersection against another
label or a header. Pair counts are label/label and label/header. Folded headers include the whole
card; open headers use their band. Header text is excluded from its own band. Edge labels use
Cytoscape's label-only bounds; SVG title/count text uses its font, anchor, offsets and clipping.
The two texts in a colliding class header count as two labels, one header case.

| Preset | Detail | Zoom | Before: labels (pairs / header pairs) | After |
| --- | --- | --- | --- | --- |
| Boltons | Modules | 1.0 | 101 (180 / 47) | 0 (0 / 0) |
| Boltons | Modules | 0.2 | 211 (449 / 119) | 0 (0 / 0) |
| Boltons | Functions | 1.0 | 0 (0 / 0) | 0 (0 / 0) |
| Boltons | Functions | 0.2 | 2 (1 / 0), one class header | 0 (0 / 0) |
| Demo | Modules | 1.0 | 2 (0 / 2) | 0 (0 / 0) |
| Demo | Modules | 0.2 | 2 (0 / 2) | 0 (0 / 0) |
| Demo | Functions | 1.0 | 0 (0 / 0) | 0 (0 / 0) |
| Demo | Functions | 0.2 | 0 (0 / 0) | 0 (0 / 0) |

The raw 449 overview pairs differ from route-cost's 448 by one. The captured camera and raw bounds
are retained: one pair intersects by only 1.14e-13 CSS px horizontally (floating-point precision).
Neither distinct-label totals nor the zero-after result depends on that numerical edge case.

Uncommitted evidence is in `.shots/edge-labels/`: `audit.mjs`, the frozen pre-change served modules,
`before.json`, `after.json`, `capture.mjs`, `captures.json`, and four `boltons-*-dark.png` captures
(start, pointer on an aggregate, selected listutils module, Functions class header at 0.2).
Day is unavailable on the baseline: `getActiveTheme` rejects it and the shot plan disables it.
No day screenshot is presented as a product capture. `make shots` retains its explicit skips.

Browser tests exercise actual pointer entry/leave, selection/clear, unselectable edges, unchanged
routes/styles/positions/camera/focus during hover, and persistence across incoming events.
They compare rendered title/count bounds against the gap token, with and without a chevron.
Unit tests cover measurement caching, uncached fallback, font loading and recovery/disposal.

The existing GraphView lifecycle and GraphView.test.ts exceed the size review signals; keeping
font readiness, hover cleanup and renderer ownership together avoids an unrelated lifecycle refactor.
The browser interaction stays one scenario so pointer, selection and geometry share a graph.

Validation: `make check` passed (4863 Python, 1083 frontend unit, 58 shot-tool and 17 boundary
tests; 6 skips). `make test-browser` passed (14 launcher and 175 browser tests); `make e2e`
passed (15). `make shots` captured 11 dark scenes; the detector found nothing. Day stays skipped.
Logs are retained in `.shots/edge-labels/`. No model credits were used.

Read: AGENTS.md; DESIGN.md graph, folding, departures and motion; styles/layers and their tests;
selection.ts; GraphView.tsx; the Boltons preset and capture helper; Impeccable's complete
`reference/typeset.md`, `reference/layout.md` and `reference/craft-floor.md`.
