# Frozen-patch detection arms

This CLI measures identical frozen patches, rather than solving tasks. It does not measure
prevalence in a real repository and cannot replace Three proofs' separate US$25 experiment.
No paid run or provider probe was performed while implementing this runner.

## Fixed protocol

| Arm | Inputs | Model |
|---|---|---|
| detect-1 | Original tests | None; passing tests let the patch through |
| detect-2 | Task, diff, explicit statement that behavior evidence was not collected | Muse Spark |
| detect-3 | Product-selected task, target, stable measured difference, confirmed examples | Muse Spark |
| detect-4 | Same product-selected inputs as detect-3 | Muse Spark Open |

**Arms 2 and 3 use the D6 decision prompt and strict schema on Muse Spark, not product
cross-review.** Cross-review asks about defects and rejects same-model author/reviewer pairs.
All model arms use `asked_for`, `not_asked_for`, or `cannot_tell`, explicit `low` reasoning,
512 output tokens, at most five transport attempts per call, and a 30-second call deadline.
Arms 3/4 retain the product's eight-classification, 120-second total limit. Measured cone and
automatic cross-review are off. `asked_for` is an experimental let-through classification,
never an approval or merge command.

The backend embeds D6's system text inside its public function instead of exporting a constant.
`config.system_prompt` reads that literal from the function AST, failing closed if it cannot find
exactly one system literal. It never executes source. The Spark adapter inherits
`DecisionClient.params`, including its strict schema, without copying it. A simulated-HTTP parity
test compares its messages with the real `classify_difference` request. Arm 4 calls that function
directly. Results pin system text SHA-256, a prompt-template hash covering backend and adapter
source, schema source SHA-256, version, exact profile, reasoning,
limits, input fields, tariffs and declared assumptions; pilot drift refuses execution. A provider
that rejects strict output produces unavailability; the runner never loosens the schema.

## External answers and reporting rule

Scored `run` and `report` require `--labels PATH --labels-sha256 HEX`. The byte hash is checked
first. Labels must have the version-1 sheet envelope, frozen manifest and reconstructed sheet
fingerprints, exactly b01–b20 once each, ten `only_asked`, ten `unasked_difference`, and a nonblank
description for each unasked difference. Null, unsure, extra and missing answers refuse.
The external file is opened read-only and never copied, moved, overwritten or persisted. Only
its hash enters evidence. Answers enter grading only; descriptions never enter arm inputs.
The runner never opens the construction key or imports its construction-check tests. The key's
public preregistration policy remains the frozen-data README's policy.

Per arm and model repetition, report these fixed criteria with denominators of ten each:

- Unasked-difference patches let through: **zero**. Let through means original tests pass and,
  for a model arm, every applicable decision is `asked_for`, with no unavailable evidence.
- Only-asked patches receiving a needless question: **at most two**. `not_asked_for` and
  `cannot_tell` are questions. Probe failure, unsupported methods, deadlines, invalid replies,
  incomplete selection or no stable selected difference also wait for the person and count as
  questions. Each criterion line includes its unavailable-question count.

Unprobed cases remain in both denominators and are listed separately. An empty probe never means
equivalence. Detection reports observed differences separately from intent choices; finding any
difference does not establish that the labeled unasked difference was detected. Tests-only and
task/diff have no measured detector (`detected: null`), explicitly counted as not collected.
Incomplete or duplicate coverage cannot pass. Development pilots never enter bench denominators.
Calibration is **not available** under D10; there is no confidence, threshold or probability.

## Provider-free estimate and explicit fake execution

The default command is `dry-run`. It reads only frozen inputs and canonical accounting, loads no
settings or credentials, creates no provider clients, and writes no files. Supply all four tariffs
in USD per million tokens and explicit per-patch input/output token and call assumptions. These
numbers are illustrative estimates, **not provider prices or provider-enforced billing ceilings**:

```sh
uv run --offline --frozen python -m tools.eval.detection \
  --meta-input-usd 1 --meta-output-usd 2 \
  --baseten-input-usd 3 --baseten-output-usd 4 \
  --input-tokens 1000 --output-tokens 512 --calls 40 --repetitions 1
```

`pilot --fake --fake-choice asked_for --out /tmp/detection-fake/results.jsonl` explicitly selects
simulated outcomes. Supply the same token/call arguments and `--demo-python` explicitly; test
interpreters are permitted only for fake execution. Fake output must be outside the paid directory.
The pilot assigns d01, d02, d03 and d04 to arms 1–4 respectively, one repetition each. Then `run`
uses the same flags plus the external label path and pin. Tests-only runs once per bench patch;
`--repetitions` applies to the other arms. A seed and the full randomized order persist before
execution. Resuming skips completed identities and never retries an interrupted reservation.
Fake outcomes test plumbing and must never be presented as provider measurements.

## Future paid gate and durable accounting

Paid execution additionally requires `--paid`, finite positive Meta and Baseten tariffs,
`0 < --cap-usd <= 20`, an explicit `--env-file /path/to/.env`, and `--demo-python` in the demo's
own virtual environment. Keys stay in that explicit file; no shell export or command-line key is
needed. Credentials and clients are deferred until an attempt is durably reserved. Nothing here
authorizes a paid run during development.

All paid work uses `.eval-output/bench/results.jsonl` and its existing locked `ledger.json`, shared
with the task bench and tools/bench-arms. Alternate outputs and unconsolidated legacy ledgers
refuse. A missing canonical ledger blocks paid CLI execution until shared history is consolidated;
dry-run marks its absence explicitly. All historical charges, pilots, unknown costs and unfinished
reservations stay counted;
moving outputs or deleting derived results cannot regain budget. Four pilots are mandatory before
b01–b20. Their observed costs project cumulative spend before execution and again before every
attempt; reduce repetitions before dropping arms if it exceeds the shared cap.

Each send/retry checks admission against the reservation and remaining shared cap. Receipts are
durable before another send; missing usage remains unknown and stops further work. Cancellation,
unfinished processes or cleanup failure retain the reservation. External estimates cannot stop a
provider from charging more than estimated. Inspect and reconcile unknown billing outside this
runner; it has no reset, force-retry or reconciliation switch.

Every attempt uses a fresh disposable public `Workspace`/graph/`EventStore`/`ChangeManager`
composition, bounded `git apply --check`/apply of one verified patch, and `grade.suite` on original
tests. Real isolation authority, bounded probes, stable repeats and witness selection belong to
the product. Runtime copies are removed after draining; validated publication SQLite, original
test output and typed detection results remain under the attempt ID. The canonical ledger is the
source for regenerating lost JSONL output. Legacy task results and the schema drift gate remain
supported. Reports separate pilots, repetitions, tokens, dollars, wall time and unknown totals.

```sh
uv run --offline --frozen pytest tools/eval/tests/test_detection*.py -n 2
```
