# Camera browser test timing

## Pre-merge measurements

The measurements and conclusions in this section predate the merge of main `8fe87a1`.

Measured 2026-10-27 14:34 ART (UTC-3) in the camera-test-flake clone. Baseline: `beda135`.

### Method

Each row is a fresh Vitest process running all four cases of
`frontend/src/app/camera.browser.test.ts`. Ten runs were sequential and isolated;
three more started alongside a fresh `make check` each, before and after the change.
For every loaded row, the check process was still running when the browser command
finished, and `make check` eventually exited 0. No model API was used.

Command, from `frontend/` (use a distinct output file for each run):

```sh
pnpm exec vitest run --project browser src/app/camera.browser.test.ts \
  --reporter=json --outputFile=/tmp/camera-run.json
```

For loaded measurements, start `make check` in the repository root as a background
process, run the browser command immediately, then wait for that exact process.
Benchmark output was captured to temporary logs; the final hand-back gates run
directly, without pipes or output redirection, on the committed tip.

Times are seconds. C1: animated open/close; C2: reduced motion;
C3: cone visibility after pan/zoom/Fit; C4: keyboard focus.
**Sum** adds the four case durations from the Vitest JSON reporter.
**Wall** measures the entire command with a monotonic clock, including process,
Vite, Chromium and module startup. Sharing the page moves module startup into
`beforeAll`, so both measures are reported.

### Baseline caveat

The supplied branch already assigned 30 s explicitly to each case. These runs did
not reproduce the reported 5 s timeout. Instead, C1 failed in all 13 baseline runs:
it expected visible cone nodes in panel mode, although the harness supplied the cone
only in review mode. That failure exits C1 before its close/restore assertions, so
its baseline timings cover less work. The initial optimization checked absence in
panel mode and presence in review. Review found this weakened C1: the fix-round
measurements below restore the live cone in panel mode and the non-empty assertion,
including a named caller beyond the frame. All camera assertions now run with the
normal 5 s limit and no custom per-case timeout.

### Isolated runs

#### Before

| Run | C1    | C2    | C3    | C4    | Sum    | Wall   | Result    |
| --- | ----- | ----- | ----- | ----- | ------ | ------ | --------- |
| 1   | 3.341 | 1.926 | 3.213 | 2.220 | 10.700 | 11.950 | C1 failed |
| 2   | 2.792 | 1.706 | 3.547 | 1.732 | 9.777  | 11.260 | C1 failed |
| 3   | 2.563 | 1.750 | 2.919 | 1.490 | 8.721  | 9.801  | C1 failed |
| 4   | 2.242 | 1.488 | 2.438 | 1.435 | 7.604  | 8.506  | C1 failed |
| 5   | 2.234 | 1.524 | 2.467 | 1.261 | 7.487  | 8.366  | C1 failed |
| 6   | 1.995 | 1.275 | 2.174 | 1.218 | 6.663  | 7.431  | C1 failed |
| 7   | 1.924 | 1.286 | 2.193 | 1.212 | 6.614  | 7.366  | C1 failed |
| 8   | 1.950 | 1.289 | 2.240 | 1.264 | 6.743  | 7.476  | C1 failed |
| 9   | 2.070 | 1.423 | 2.422 | 1.404 | 7.320  | 8.122  | C1 failed |
| 10  | 2.172 | 1.466 | 2.437 | 1.356 | 7.430  | 8.282  | C1 failed |

#### Initial optimization (before review)

| Run | C1    | C2    | C3    | C4    | Sum   | Wall  | Result |
| --- | ----- | ----- | ----- | ----- | ----- | ----- | ------ |
| 1   | 1.180 | 0.578 | 1.167 | 0.509 | 3.433 | 5.224 | pass   |
| 2   | 1.146 | 0.490 | 1.143 | 0.515 | 3.293 | 5.062 | pass   |
| 3   | 1.274 | 0.567 | 1.237 | 0.574 | 3.652 | 5.388 | pass   |
| 4   | 1.249 | 0.537 | 1.256 | 0.520 | 3.563 | 5.619 | pass   |
| 5   | 1.141 | 0.485 | 1.073 | 0.493 | 3.192 | 5.365 | pass   |
| 6   | 1.112 | 0.432 | 1.060 | 0.449 | 3.054 | 4.749 | pass   |
| 7   | 1.046 | 0.507 | 1.107 | 0.472 | 3.132 | 4.840 | pass   |
| 8   | 0.952 | 0.424 | 1.167 | 0.563 | 3.106 | 4.689 | pass   |
| 9   | 0.973 | 0.416 | 1.077 | 0.534 | 3.000 | 4.799 | pass   |
| 10  | 1.137 | 0.491 | 1.050 | 0.498 | 3.177 | 4.831 | pass   |

### Runs alongside make check

| Phase  | Run | C1    | C2    | C3    | C4    | Sum   | Wall  | Result    |
| ------ | --- | ----- | ----- | ----- | ----- | ----- | ----- | --------- |
| before | 1   | 2.416 | 1.631 | 2.853 | 1.525 | 8.425 | 9.451 | C1 failed |
| before | 2   | 2.282 | 1.476 | 2.366 | 1.524 | 7.648 | 8.552 | C1 failed |
| before | 3   | 2.162 | 1.465 | 2.473 | 1.658 | 7.758 | 8.835 | C1 failed |
| after  | 1   | 1.128 | 0.547 | 1.259 | 0.532 | 3.466 | 5.553 | pass      |
| after  | 2   | 1.532 | 0.668 | 1.419 | 0.582 | 4.201 | 6.739 | pass      |
| after  | 3   | 1.709 | 0.665 | 1.672 | 0.700 | 4.746 | 7.861 | pass      |

### Interpretation

- Isolated: median case sum 7.458 → 3.185 s (57.3% lower);
  slowest initially optimized individual case 1.274 s.
- Loaded: median case sum 7.758 → 4.201 s (45.9% lower);
  slowest initially optimized individual case 1.709 s.

The avoidable work was repeated browser/module startup, the unrelated dot-field
WebGL initialization, and wall-clock animation/viewport frames. The test now loads
its graph-only modules once, mounts and unmounts a fresh graph per case, and uses
the controlled callback queue pattern from `GraphView.test.ts`. Both frame timestamps
and `performance.now()` advance explicitly. A midpoint assertion verifies that the
real Cytoscape animation progresses before the final frame completes it. DOM/font
readiness is condition-polled independently of the controlled animation queue.

The initial optimization dispatched button handlers explicitly. The fix round below
restores real Playwright clicks and paints frames until each click completes its
visibility, stability and hit tests. Visibility reporting is flushed after each
navigation action; background drag and keyboard input also remain real browser input.

The baseline already had a saved preset layout and checks forbidding Monaco requests;
those remain. The 2d Cytoscape renderer remains real. WebGL drawing is covered by
`dotField.browser.test.ts`, including its plain-ground fallback. No application code,
contracts, dependencies, README or DEVLOG changed.

### Fix round 1: restored regression coverage

Measured 2026-10-27 14:54 ART (UTC-3), with the same commands and baseline above.
The panel fixture now supplies `liveCone` and its lit ids, independently of review's
card and camera cone. C1 requires non-empty cone nodes, the named caller in that
cone and that caller outside the changed node's frame, while retaining midpoint,
zoom, centering and close/restore checks. C3 uses real Playwright clicks while a
controlled frame pump lets actionability finish; all eight button events must be
`isTrusted`. A fifth case seeds stale errors and editor requests and verifies that
`withPage` clears both. Its diagnostic duration is **D** below.

Each regression was checked with its defect reintroduced separately: omitting the
live cone, dispatching synthetic clicks, or removing the diagnostic resets each
made its targeted browser test exit 1. All three fixes were then restored.

**Sum** still adds C1–C4 for comparison with the original four-case baseline;
**Wall** now includes all five cases and shared setup. All 13 runs passed 5/5;
each loaded run overlapped a fresh `make check` that subsequently exited 0.

#### Isolated after review

| Run | C1    | C2    | C3    | C4    | D     | Sum   | Wall  |
| --- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| 1   | 1.218 | 0.677 | 1.313 | 0.772 | 0.415 | 3.980 | 5.832 |
| 2   | 1.257 | 0.708 | 1.363 | 0.740 | 0.415 | 4.067 | 5.911 |
| 3   | 1.264 | 0.725 | 1.398 | 0.442 | 0.478 | 3.829 | 5.745 |
| 4   | 1.354 | 0.696 | 1.308 | 0.771 | 0.434 | 4.129 | 6.135 |
| 5   | 1.272 | 0.736 | 1.317 | 0.441 | 0.408 | 3.766 | 5.673 |
| 6   | 1.351 | 0.802 | 1.475 | 0.837 | 0.478 | 4.464 | 6.369 |
| 7   | 1.348 | 0.716 | 1.569 | 0.892 | 0.501 | 4.525 | 6.635 |
| 8   | 1.436 | 0.865 | 1.696 | 1.226 | 0.680 | 5.223 | 7.695 |
| 9   | 1.724 | 0.958 | 1.676 | 1.022 | 1.188 | 5.380 | 8.774 |
| 10  | 1.692 | 0.846 | 1.505 | 0.858 | 0.421 | 4.901 | 7.544 |

Median C1–C4 sum: 7.458 → 4.297 s (42.4% lower).
Slowest original case: 1.724 s; slowest including D: 1.724 s.

#### Alongside make check after review

| Run | C1    | C2    | C3    | C4    | D     | Sum   | Wall  |
| --- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| 1   | 1.758 | 0.980 | 1.478 | 0.489 | 1.141 | 4.705 | 7.849 |
| 2   | 1.558 | 0.815 | 1.379 | 0.872 | 0.517 | 4.623 | 6.938 |
| 3   | 1.864 | 1.488 | 1.704 | 0.560 | 0.551 | 5.617 | 8.205 |

Median C1–C4 sum: 7.758 → 4.705 s (39.4% lower).
Slowest original case: 1.864 s; slowest including D: 1.864 s.

## Post-merge measurements

Measured 2026-10-27 16:49 ART (UTC-3) on merge commit `2135454`, whose parents
are branch tip `a4f6423` and main `8fe87a1`. The documentation-only commit that
records these results does not change the measured test or fixture.

### Accepted camera expectations

Only the three conflicting expectations in the original animated panel case changed:

- Zoom: previously `toBeCloseTo(wide.zoom, 6)` (1); now exactly
  `11 / Math.min(framed.labelSizes.name, framed.labelSizes.state)`, with the active
  token-derived floor itself required to equal `11 / 16` (0.6875).
- Horizontal center: previously the changed node's x was within `toBeCloseTo(..., 0)`
  of `(open.leftInset + open.size.width) / 2`; now `assertPanelFrame` checks
  camera-frame's horizontal framing and centering rule.
- Vertical center: previously the changed node's y was within `toBeCloseTo(..., 0)`
  of `open.size.height / 2`; now the same oracle checks camera-frame's vertical rule.

This is the existing independent acceptance oracle used by camera-frame's browser
and screenshot tests, including its unchanged tolerances. It selects the whole
cone when legible, then distance 1, then the nearest distance-1 boxes that fit at
the floor. It requires those boxes inside the clear area and checks the nearest
uncut module view to the bounded center, falling back to bounded centering when
no uncut view fits. No loose zoom bound or additional centering range was added.
All other assertions from both parents remain, including non-empty live callers,
the named caller outside the frame, intermediate easing, stable node positions,
restoration, trusted clicks, focus and diagnostic reset checks.

### Method and scope

The command and monotonic wall-time method are the same as in the pre-merge
section. Each fresh process now runs all **17 cases** in the merged file: the
five branch cases plus twelve from main. Ten runs were sequential and isolated;
three more each overlapped a fresh `make check`, which was still running when
the browser command finished and subsequently exited 0. Every run passed 17/17
with the normal 5 s per-case limit. No model API was used.

C1–C4 and **D** retain their pre-merge meanings. **Sum** still adds C1–C4;
**All 17** sums every case, including D and main's additional cases.
**Wall** includes all 17 cases and shared process/browser/module setup. Times
are seconds; sums use the unrounded reporter values. The changed camera behavior
and expanded suite mean whole-command times cover more work than pre-merge.

### Isolated after merge

| Run | C1 | C2 | C3 | C4 | D | Sum | All 17 | Wall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.845 | 0.716 | 1.078 | 0.294 | 0.264 | 2.932 | 12.141 | 13.502 |
| 2 | 0.941 | 0.763 | 1.109 | 0.285 | 0.257 | 3.098 | 12.234 | 13.605 |
| 3 | 0.905 | 0.779 | 1.089 | 0.643 | 0.288 | 3.416 | 12.643 | 13.963 |
| 4 | 0.928 | 0.743 | 0.998 | 0.302 | 0.407 | 2.971 | 12.390 | 13.716 |
| 5 | 0.926 | 0.754 | 1.075 | 0.317 | 0.321 | 3.071 | 12.309 | 13.644 |
| 6 | 0.865 | 0.716 | 1.032 | 0.590 | 0.264 | 3.204 | 12.911 | 14.306 |
| 7 | 0.930 | 0.759 | 1.098 | 0.298 | 0.252 | 3.085 | 12.357 | 13.711 |
| 8 | 0.934 | 0.777 | 0.990 | 0.561 | 0.254 | 3.263 | 13.075 | 14.491 |
| 9 | 0.974 | 0.779 | 0.997 | 0.585 | 0.258 | 3.334 | 12.766 | 14.107 |
| 10 | 0.971 | 0.767 | 1.047 | 0.551 | 0.280 | 3.336 | 12.696 | 14.107 |

Median C1–C4 sum: **3.151 s**; median all-17 sum: **12.517 s**;
median wall: **13.839 s**. Slowest individual case across all 17: **1.489 s**.

### Alongside make check after merge

| Run | C1 | C2 | C3 | C4 | D | Sum | All 17 | Wall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.904 | 0.766 | 1.261 | 0.339 | 0.315 | 3.270 | 13.960 | 15.456 |
| 2 | 0.922 | 0.748 | 1.101 | 0.658 | 0.279 | 3.429 | 13.582 | 15.015 |
| 3 | 0.995 | 0.803 | 1.094 | 0.354 | 0.289 | 3.246 | 13.314 | 14.813 |

Median C1–C4 sum: **3.270 s**; median all-17 sum: **13.582 s**;
median wall: **15.015 s**. Slowest individual case across all 17: **1.572 s**.

### Review signal

`camera.browser.test.ts` is 761 lines after retaining both parents' regression
cases and their shared browser/frame/input helpers in one test file. Keeping the
shared setup together preserves the measured one-browser-per-process method;
the file-size signal remains for coordinator review.

## Fix round: isolated browser fixture caches

Measured 2026-10-27 ART (UTC-3), starting from `bab4490`. This round changes
fixture infrastructure only. Camera, dot-field and task-box fixtures now use
`.shots/camera-vite-cache`, `.shots/dot-field-vite-cache` and
`.shots/task-box-vite-cache`, respectively. The browser project has `maxWorkers: 2`.

Before changing either setting, two fresh `pnpm test:browser` processes each
passed all 72 cases across nine files: **2/2 runs, 144/144 cases, zero failures**.
They used the shared `.shots/app-vite-cache` and the uncapped browser project.
The cache collision did not reproduce in these runs; isolation removes concurrent
writes to the same dependency cache rather than claiming a measured speedup.

Two new checks in `frontend/shots/vite-config.test.ts`, included in `pnpm test`
and `make check`, guard the distinct fixture cache paths and the browser project's
two-worker limit. Before the fix, both failed (one unique cache instead of three;
`maxWorkers` undefined instead of 2). With the fix, all four config checks pass.

The camera remeasurement uses the same JSON-reporter and monotonic wall-time
method above: ten fresh isolated processes and three more each alongside its own
fresh `make check`. Full-suite runs are separate from those camera measurements.
No timeout, assertion, retry or input-driving behavior changes in this round.

### Full browser suite before and after isolation

Each row aggregates two fresh full-suite processes. All nine files ran each
time, so camera, dot-field and task-box were tested alongside the other fixture
families rather than only in a camera-only process. Caches were not deleted
between runs; the first post-fix run populated the three new cache directories.

| Configuration | Passing runs | Passing cases | Failed cases |
| --- | --- | --- | --- |
| Shared cache, no project worker cap | 2/2 | 144/144 | 0 |
| Separate caches, `maxWorkers: 2` | 2/2 | 144/144 | 0 |

Post-fix full-suite wall times were 149.589 s and 125.225 s. These two samples
establish passing coverage with isolation; they are not evidence of a lower
failure rate. The two-worker cap intentionally limits concurrent fixture servers
and Chromium instances, which can increase whole-suite wall time.

### Camera alone after fixture isolation

| Run | Passing cases | All 17 (s) | Wall (s) | Slowest case (s) |
| --- | --- | --- | --- | --- |
| 1 | 17/17 | 19.646 | 21.885 | 2.301 |
| 2 | 17/17 | 19.368 | 21.149 | 1.978 |
| 3 | 17/17 | 26.915 | 29.655 | 2.661 |
| 4 | 17/17 | 29.870 | 35.910 | 3.823 |
| 5 | 17/17 | 22.739 | 26.271 | 3.051 |
| 6 | 17/17 | 18.799 | 20.998 | 2.160 |
| 7 | 17/17 | 24.057 | 26.292 | 2.588 |
| 8 | 17/17 | 27.668 | 31.302 | 3.139 |
| 9 | 17/17 | 25.497 | 28.960 | 2.474 |
| 10 | 17/17 | 36.516 | 42.192 | 3.839 |

Median all-17 sum: **24.777 s**; median wall: **27.626 s**.
Ten of ten runs passed, 170/170 cases. Slowest individual case: **3.839 s**,
within the unchanged 5 s limit.

### Camera alongside make check after fixture isolation

| Run | Passing cases | All 17 (s) | Wall (s) | Slowest case (s) | Check exit |
| --- | --- | --- | --- | --- | --- |
| 1 | 17/17 | 39.585 | 46.264 | 4.723 | 0 |
| 2 | 17/17 | 16.582 | 18.433 | 1.814 | 0 |
| 3 | 17/17 | 32.064 | 38.396 | 3.782 | 2 |

The check process was still running when each camera process finished. All
three camera runs passed, 51/51 cases. Median all-17 sum: **32.064 s**; median
wall: **38.396 s**. Slowest individual camera case: **4.723 s**, below 5 s.

The third check failed in the unrelated live HTTP API test
`test_stop_an_idle_agent`: its reused connection closed before the Stop response
(`httpx.RemoteProtocolError`). The other 2316 Python cases passed, as did all
frontend checks. This failure is retained here rather than counted as a passing
loaded check. The API fixture now opens a fresh HTTP connection per command,
avoiding reuse across long event waits and Uvicorn's idle connection expiry.
A new socket-port regression fails with connection reuse and passes with fresh
connections; all five existing Stop cases also pass with the fixture fix.

### Validation after the HTTP fixture fix

The first additional validation was interrupted by a machine restart; its
temporary logs were lost and it is not counted as a completed run. A fresh
camera/check pair was started after the restart at 19:11 ART (UTC-3), using
the same commands and measurement method.

| Passing cases | All 17 (s) | Wall (s) | Slowest case (s) | Check exit |
| --- | --- | --- | --- | --- |
| 17/17 | 26.487 | 29.776 | 2.787 | 0 |

The check was still running when the camera process finished and subsequently
passed: 2318 general Python tests (including the new connection regression),
17 frontend-boundary tests, 646 frontend unit tests and 42 tooling tests.
Across completed loaded samples, camera passed 68/68 cases; three checks passed
and the pre-fix failed check remains recorded above. No test retry or raised
timeout was introduced.
