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
it expected visible cone nodes in panel mode, although the harness supplies the cone
only in review mode. That failure exits C1 before its close/restore assertions, so
its baseline timings cover less work. The fix checks the absence of the cone in
panel mode and its presence in review; all original camera assertions still run.
The final tests use the normal 5 s limit, with no custom per-case timeout.

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

### After

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
  slowest final individual case 1.274 s.
- Loaded: median case sum 7.758 → 4.201 s (45.9% lower);
  slowest final individual case 1.709 s.

The avoidable work was repeated browser/module startup, the unrelated dot-field
WebGL initialization, and wall-clock animation/viewport frames. The test now loads
its graph-only modules once, mounts and unmounts a fresh graph per case, and uses
the controlled callback queue pattern from `GraphView.test.ts`. Both frame timestamps
and `performance.now()` advance explicitly. A midpoint assertion verifies that the
real Cytoscape animation progresses before the final frame completes it. DOM/font
readiness is condition-polled independently of the controlled animation queue.

Button handlers are dispatched explicitly so Playwright actionability does not
wait for paused frames; the Cytoscape background drag and keyboard input remain
real browser input. Visibility reporting is flushed after each navigation action.

The baseline already had a saved preset layout and checks forbidding Monaco requests;
those remain. The 2d Cytoscape renderer remains real. WebGL drawing is covered by
`dotField.browser.test.ts`, including its plain-ground fallback. No application code,
contracts, dependencies, README or DEVLOG changed.
