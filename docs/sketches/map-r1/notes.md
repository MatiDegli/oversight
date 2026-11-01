# Map sketch, rounds 1, 1b and 1c

`map.html` is bold-r5 `life.html` (shell, LED field, contours, light, tokens, type) plus `work/map.js` and `work/map.css`, inlined by `work/build.py`. `work/gen.py` builds `work/data.json` from the Boltons d31d821 preset and snapshot (real modules, function counts, exact and possible calls after the builtin filter) and the demo fixture. Agents, the decision and the cone's seed are sketch data (A changes `iterutils.is_iterable`, B edits `strutils.slugify`); cone members are real exact callers to depth 2.

## States and shots (`map.html#state`, add `?theme=day`)
| Hash | Shot | What |
| --- | --- | --- |
| `#fit`, `#fit-notests` | fit-dark, fit-notests-dark | Boltons fitted, with tests and with tests hidden: map |
| `#map` | map-dark, map-day, map-1280-dark | rest: one panel per folder, equal circles, no lines |
| `#map-hover` | map-hover-dark | pointer on typeutils.py: its calls in and out with counts; its panel lifts |
| `#map-groups` | map-groups-dark, map-groups-day | groups named by Oversight; tests, misc and docs stay folders |
| `#map-cone` | map-cone-dark | A's cone: lines from the changed module to what it reaches, badges, other names hidden |
| `#cross` | cross-dark | crossfade midpoint at the enter threshold, cards and map at 50% |
| `#group`, `#demo` | group-dark, demo-1280-dark | after a boltons group click; the demo at 1280x800: both cards |
| `#layout` | layout-dark | today's preset beside the grouped one, same zoom |

## Round 1c: panels, cells, equal circles, no lines at rest
- Every circle has the same size; the function count lives only in "N functions".
- Each group of two or more modules is a panel: `--card` at 60% over the ground (the LED light still shows through), a `--node-border` border, and a header strip inside its top. The strip holds name, module count, team rings, decision, and either "tested by N calls" or, in the cone, "N of 17 in group". Pointing at a panel lifts its border to `--edge`.
- Inside a panel, modules sit on a regular grid of equal cells (446 x 220 world px: widest card 326 + 120 gutter; card 70 + 150). Circle and name are centered together in the cell. A name never leaves its cell; long names end in "…" with the full name on hover.
- Ground between groups: 340 world px sideways, 460 stacked (cells to cells). That is 82 to 121 px at the drawn zooms, so at least 48 px of field shows between panel edges.
- Panels in one band share a top edge, so their header strips line up.
- docs has one module, so it draws no panel (DESIGN 3) and its name reads "docs/conf.py".
- Lines: none at rest. In `#map-hover`, the pointed module's exact calls in and out show with arrows and ×N chips placed off names, plus its possible calls, faint. In `#map-cone`, lines run from the changed module to the modules its change reaches.
- "Tested by" counts are exact calls from tests into the group: boltons 434; Collections 159, Files and network 110, Functions and debugging 75, Time and numbers 51, Text 39.

## Thresholds and levels (enter below 64.7% = 11/17, leave above 68.75% = 11/16)
- Leave is the product start camera's floor (`graph/camera.ts` `changeZoom().min`). The fit compares against enter. Fits use the product rule on card boxes (FIT_PADDING 40). The drawing may then zoom out so the panels and header strips clear the heading and legend.
- 1920x1080 (stage 1464x1004):
  - With tests: grouped 30.3% (was 34.1%), today's preset 40.1%. Both land on the map.
  - Tests hidden: grouped 50.4% (was 63.2%), map; drawn at 39.7% to clear the heading and legend. Today's preset 72.6%, cards.
  - `#group`: the framed group is 47.1% (was 67.9%), raised to 68.75% centered: cards. About seven of nine rows show, and the top row sits under the heading.
  - `#map-groups`: fit 30.3%, drawn 26.4% (three bands of header strips).
- 1280x800 (stage 1168x724): `#map` 23.8% (was 26.8%), map. A boltons click frames at 31.8% and rises to 68.75%. `#demo` is unchanged: 65.4%, cards.
- DESIGN says about 65% for the no-tests fit; today's preset gives 72.6%. The build measures it in the product and corrects DESIGN.md.
- Cards move more under the new spacing (`#layout`, tests hidden): median 390 px at 100%, jsonutils 1,583, misc and docs 2,246 to 3,331 px.

## Named groups (#map-groups), from module names and docstrings
- **Text**: strutils, formatutils, easterutils.
- **Collections**: dictutils, listutils, setutils, queueutils, iterutils, cacheutils, namedutils, tableutils.
- **Files and network**: fileutils, ioutils, jsonutils, pathutils, socketutils, urlutils, mboxutils.
- **Time and numbers**: timeutils, mathutils, statsutils.
- **Functions and debugging**: funcutils, typeutils, tbutils, debugutils, excutils, gcutils, deprutils, ecoutils, `__init__`.
- **Doubtful**: easterutils (no docstring; an Easter-egg print loop), ecoutils (a runtime ecosystem profile), deprutils (no docstring; a deprecated-module wrapper), `__init__` (only the version), tableutils (a data structure that also renders text).
- **Layout**, three bands: Collections, Functions and debugging, Files and network (3x3 each); misc, docs, Text, Time and numbers (two columns each); tests, eight across.

## Rules bent, and why
1. DESIGN "Scale and depth" 2: area by function count. Circles are equal, by the maintainer's decision.
2. DESIGN "Scale and depth" 5: lines follow Call visibility. No lines at rest; lines only for the cone and on hover, by the maintainer's decision.
3. The grouped preset is a regular grid with taller rows (220, not 150) and wider ground between groups. This moves cards and lowers every grouped fit, listed above.
4. In `#map`, tests sit right of misc, not in a band below: a band fits lower and leaves the width empty. `#map-groups` keeps tests below.
5. Group rings use the agent colors; no team color is decided. Agent modules stay folded in `#group` and `#cross`.

## Open questions
- Should the map's camera fit the panels (header strips and full cells) rather than the cards? The no-tests view is then 39.7%, not 50.4%.
- Rows of 220 give the agent line room, but a group click now shows only part of boltons at 68.75%. Would 190 be enough?
- At 1280 (23.8%) and in `#map-groups` (26.4%), many test names end in "…". Is hover enough?
- Pointing at a panel could also show that group's calls in and out; the sketch shows only module hover.
- Named groups in the preset: who places them, the model or a deterministic packing of its answer?
