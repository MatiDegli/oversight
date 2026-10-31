# Detection bench results

## What it measures

The [Fixed protocol](README.md#fixed-protocol) uses 20 frozen patches: 10 with an unasked difference and 10 that do only what was asked.
The arms are detect-1 (tests only, no model), detect-2-v2 (task and diff, Muse Spark), detect-3-v2 (product-selected behavior evidence, Muse Spark), and detect-4-v3 (the same inputs, Muse Spark Open).
Criteria per arm and repetition, denominators of ten: unasked-difference patches let through, zero; only-asked patches with a needless question, at most two.

## Main paid run

October 30, 2026, about 01:30-01:45 UTC; plan `fba5e5e7c912`; total spend US$0.0513.

| Arm | Spend (US$) | Repetition | Let through (criterion 0) | Needless questions (criterion <= 2) | Unavailable questions (let-through / needless) |
|---|---|---|---|---|---|
| detect-1 | 0 | 1 | 10/10 | 0/10 | 0/10 / 0/10 |
| detect-2-v2 | 0.0133 | 1 | 0/10 | 8/10 | 0/10 / 0/10 |
| detect-2-v2 | | 2 | 0/10 | 9/10 | 0/10 / 0/10 |
| detect-2-v2 | | 3 | 1/10 | 10/10 | 0/10 / 0/10 |
| detect-3-v2 | 0.0026 | 1, 2, 3 (identical) | 0/10 | 7/10 | 10/10 / 7/10 |
| detect-4-v3 | 0.0352 | 1, 2, 3 (identical) | 0/10 | 7/10 | 10/10 / 7/10 |

Every arm failed its criteria.

Spend per arm leaves out the pilots (about US$0.0002); the total includes them.

## Why

Read-only diagnosis, with no model call: the run measured the evidence pipeline, not the models.

- In arms 3 and 4 the model was asked in 6-7 of the 20-21 cases per repetition; the two arms made the same choice in 60 of 61 attempts.
- Every other case was decided by missing evidence: 17-18 questions per repetition came from it. The 3 only-asked patches that went through without a question also did so without a model choice. No question in either arm came from a model choice alone.
- Methods and classes were not probed; probe failures lost their real reason; observed differences were dropped before classification when a comparison was incomplete.
- Stored rows stop at 8, so functions with 25-26 differing inputs could not be covered; only one minimal example per function was classified; `compute_price` found no difference in 48 inputs.

## What changed

Evidence fixes on main, linked to their decision headings and merge commits:

- [2026-10-30 — B1: Omitted optional additions compare shared parameters](../../../DECISIONS.md#2026-10-30--b1-omitted-optional-additions-compare-shared-parameters) — merge `647d545`.
- [2026-10-30 — B2: Defaults may resolve a uniquely bound module literal](../../../DECISIONS.md#2026-10-30--b2-defaults-may-resolve-a-uniquely-bound-module-literal) — merge `647d545`.
- [2026-10-30 — B3: Classify one witness per outcome group within a function](../../../DECISIONS.md#2026-10-30--b3-classify-one-witness-per-outcome-group-within-a-function) — retain observed differences despite incomplete comparison and cover groups beyond stored rows; merge `3068b81`.
- [2026-10-30 — B4: A different public object state gets an outcome card](../../../DECISIONS.md#2026-10-30--b4-a-different-public-object-state-gets-an-outcome-card) — merge `2cfe760`.
- [2026-10-30 — Bench method scope supersedes D9's level 3 deferral](../../../DECISIONS.md#2026-10-30--bench-method-scope-supersedes-d9s-level-3-deferral) — probe methods and properties, merge `2cfe760`; construct project-object arguments, merge `8a4a2ec`.
- [2026-10-30 — Detection bench evidence fixes precede other line-A work](../../../DECISIONS.md#2026-10-30--detection-bench-evidence-fixes-precede-other-line-a-work) — publish real unprobed reasons; merge `f44dd62`.

The witness-per-outcome-group coverage rule (B3) was introduced after seeing these results.

## Free sensitivity check

After the evidence fixes, on main `ad5de55`, the same patches ran twice with the scripted fake model: once answering `asked_for` and once `not_asked_for`, at no cost.
`model_sensitive` counts paired patches whose outcome flips.

| Arm | Unasked-difference patches that flip | Only-asked patches that flip |
|---|---|---|
| detect-1 | 0/10 | 0/10 |
| detect-2-v2 | 10/10 | 10/10 |
| detect-3-v2 | 8/10 | 8/10 |
| detect-4-v3 | 8/10 | 8/10 |

The paid-run gate refused: in detect-3-v2 and detect-4-v3, b04 and b19 (unasked differences, tests passing) did not flip.
b04 appends a line to a file, and b19 falls back to `uuid4()` when `sku` is empty; a probe that compares return values and receiver state sees neither.
The maintainer [chose not to run the paid pair](../../../DECISIONS.md#2026-10-30--no-paid-h1-pair-the-free-passes-miss-its-third-condition).
The bench's counting rules are unchanged.

## What this does not show

It does not measure [prevalence in a real repository](README.md).
Arms 3 and 4 matching is a property of the evidence, not a comparison of the two models.
The fake sensitivity check is not a provider measurement.

## How to reproduce

Use the README's read-only `report` and `sensitivity` commands on existing recorded results:

```sh
uv run --offline --frozen python -m tools.eval.detection report \
  --out .eval-output/bench/results.jsonl \
  --labels "$EXTERNAL_LABELS" --labels-sha256 "$EXTERNAL_LABELS_SHA256"
uv run --offline --frozen python -m tools.eval.detection sensitivity \
  --out FIRST/results.jsonl --against SECOND/results.jsonl \
  --labels "$EXTERNAL_LABELS" --labels-sha256 "$EXTERNAL_LABELS_SHA256"
```

The result files are local and not in the repository. External labels are kept outside the repository by design and pinned by SHA-256; the variables above stand for their path and pin, and FIRST and SECOND for the existing fake-pass directories.
No new run, model call or credits were used for this summary.
