# Camera browser test timing

Measured 2026-10-27 14:34 ART (UTC-3) in the camera-test-flake clone. Baseline: `beda135`.

## Method

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

## Baseline caveat

The supplied branch already assigned 30 s explicitly to each case. These runs did
not reproduce the reported 5 s timeout. Instead, C1 failed in all 13 baseline runs:
it expected visible cone nodes in panel mode, although the harness supplied the cone
only in review mode. That failure exits C1 before its close/restore assertions, so
its baseline timings cover less work. The initial optimization checked absence in
panel mode and presence in review. Review found this weakened C1: the fix-round
measurements below restore the live cone in panel mode and the non-empty assertion,
including a named caller beyond the frame. All camera assertions now run with the
normal 5 s limit and no custom per-case timeout.

## Isolated runs

### Before

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

### Initial optimization (before review)

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

## Runs alongside make check

| Phase  | Run | C1    | C2    | C3    | C4    | Sum   | Wall  | Result    |
| ------ | --- | ----- | ----- | ----- | ----- | ----- | ----- | --------- |
| before | 1   | 2.416 | 1.631 | 2.853 | 1.525 | 8.425 | 9.451 | C1 failed |
| before | 2   | 2.282 | 1.476 | 2.366 | 1.524 | 7.648 | 8.552 | C1 failed |
| before | 3   | 2.162 | 1.465 | 2.473 | 1.658 | 7.758 | 8.835 | C1 failed |
| after  | 1   | 1.128 | 0.547 | 1.259 | 0.532 | 3.466 | 5.553 | pass      |
| after  | 2   | 1.532 | 0.668 | 1.419 | 0.582 | 4.201 | 6.739 | pass      |
| after  | 3   | 1.709 | 0.665 | 1.672 | 0.700 | 4.746 | 7.861 | pass      |

## Interpretation

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

## Fix round 1: restored regression coverage

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

### Isolated after review

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

### Alongside make check after review

| Run | C1    | C2    | C3    | C4    | D     | Sum   | Wall  |
| --- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| 1   | 1.758 | 0.980 | 1.478 | 0.489 | 1.141 | 4.705 | 7.849 |
| 2   | 1.558 | 0.815 | 1.379 | 0.872 | 0.517 | 4.623 | 6.938 |
| 3   | 1.864 | 1.488 | 1.704 | 0.560 | 0.551 | 5.617 | 8.205 |

Median C1–C4 sum: 7.758 → 4.705 s (39.4% lower).
Slowest original case: 1.864 s; slowest including D: 1.864 s.
