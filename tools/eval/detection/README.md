# Frozen-patch detection arms

This CLI measures identical frozen patches, rather than solving tasks. It does not measure
prevalence in a real repository and cannot replace Three proofs' separate US$25 experiment.
No paid run or provider probe was performed while implementing this runner.

## Fixed protocol

| Arm (role) | Inputs | Model |
|---|---|---|
| detect-1 (tests-only) | Original tests | None; passing tests let the patch through |
| detect-2-v2 (task/diff) | Task, diff, explicit statement that behavior evidence was not collected | Muse Spark |
| detect-3-v2 (behavior-Spark) | Product-selected task, target, stable measured difference, confirmed examples | Muse Spark |
| detect-4-v3 (behavior-Spark-Open) | Same product-selected inputs as detect-3-v2 | Muse Spark Open |

**Arms 2 and 3 use the D6 decision prompt and strict schema on Muse Spark, not product
cross-review.** Cross-review asks about defects and rejects same-model author/reviewer pairs.
All model arms use `asked_for`, `not_asked_for`, or `cannot_tell`, explicit `low` reasoning,
4096 completion tokens (including reasoning), at most five transport attempts per call, and a
30-second call deadline. Each model arm owns one completion allowance, used both in its hashed
configuration and the client request. It is read from the imported product D6 factory
`oversight.decision_config.configured_decision`: D6 has no public allowance constant, so a
fail-closed AST read extracts its single positive literal without constructing a client. Arm 4
sends exactly the product's allowance. The independent **512-character reply validator** and
all strict schema checks remain unchanged. The deadlines and eight-classification limit come
from imported `ReviewCardLimits` defaults (30 seconds per call, 120 seconds total); the same
arm limits feed configuration and runtime. Measured cone and automatic cross-review are off.
`asked_for` is an experimental let-through classification, never an approval or merge command.

`config.system_prompt` uses the public product `D6_SYSTEM_PROMPT` constant. The Spark adapter inherits
`DecisionClient.params`, including its strict schema, without copying it. A simulated-HTTP parity
test compares its messages with the real `classify_difference` request. Arm 4 calls that function
directly. Results pin system text SHA-256, a prompt-template hash covering backend and adapter
source, diagnostics source, strict schema SHA-256, version, exact profile, reasoning,
limits, input fields, tariffs and declared assumptions; pilot drift refuses execution. A provider
that rejects strict output produces unavailability; the runner never loosens the schema.

Each decision records bounded diagnostics: reported model (up to 256 characters), finish reason
(up to 64), raw reply character count before validation, completion and reasoning counts when
reported (otherwise null), and latency in seconds including retries and accounting. No reply
text is stored. `validation_stage` distinguishes `model`, `finish_reason`, `tool_calls`,
`empty_text`, `reply_length`, `json`, `duplicate_key`, `object`, `fields`, `choice`,
`empty_choices`, `choice_count`, `message`, `refusal`, `legacy_tool_call`, transport, deadline,
product admission/unavailability codes, and `none` on success. SDK timeouts also record
`deadline`. Tests-only and unprobed nodes have no decision diagnostics, never fabricated zeros.

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

The report splits question sources using recorded results only, without model calls:
each question has exactly one source, in order: `questions_from_tests` when tests fail,
`questions_from_evidence` when tests pass but evidence is unavailable, and `questions_from_model`
when tests pass with no unavailable evidence. These three counts sum to `questions`; a question
on the tests-only arm comes from failing tests. `model_asked` counts patches with at least one
recorded decision; `unavailable_questions` counts questions with any unavailability, even when
tests failed or a decision was recorded. `unavailable_causes` counts patches with each cause once,
regardless of repeated reasons or nodes, in a fixed order (absent causes are omitted; unknown
reasons count as `other`). Causes can overlap, so their counts need not sum to the question count.
Both criterion lines append the three source counts; criteria and denominators remain unchanged.

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
  --input-tokens 1000 --output-tokens 512 --calls 8 --repetitions 1
```

`pilot --fake --fake-choice asked_for --out /tmp/detection-fake/results.jsonl` explicitly selects
simulated outcomes. Supply the same token/call arguments and `--demo-python` explicitly; test
interpreters are permitted only for fake execution. Fake output must be outside the paid directory.
The pilot assigns d01, d02, d01 and d01 to the current arms 1–4 respectively, one repetition
each. Both behavior pilots use d01's top-level `shipping_cost`, whose stable measured difference
reaches classification. In d02, `price_breaks` adds an opt-in parameter defaulting to zero;
default calls have no behavior difference, so a behavior pilot sends no model call. Supported
methods are probed: b09 (`Inventory.reserve`) compares 24 inputs, b14 (`Customer.email` setter)
2, and b19 (`Product.label`) 2. The observation documented on main `cc023c8` was that b17
(`Cart.add`), lacking a supported literal `Product` argument, and b19's changed module node,
not a callable probe target, both published "probe unavailable". After class-args (`8a4a2ec`)
and probe-reasons (`f44dd62`), the `Product` argument may be constructible and reports use
recorded unprobed facts with allowlisted phrases; no new outcome is claimed without a run. Frozen patches,
ordering, manifest and labels remain unchanged; unprobed nodes stay in the denominators. Then `run`
uses the same flags plus the external label path and pin. Tests-only runs once per bench patch;
`--repetitions` applies to the other arms. A seed and the full randomized order persist before
execution. Resuming skips completed identities and never retries an interrupted reservation.
Fake outcomes test plumbing and must never be presented as provider measurements.

Run the fake measurement in two sequential passes, `--fake --fake-choice asked_for` and
`--fake --fake-choice not_asked_for`, with separate output directories. Compare their recorded
ledgers using `sensitivity --out FIRST/results.jsonl --against SECOND/results.jsonl` with the
same `--labels` and `--labels-sha256` pin as `report`: `model_sensitive` counts paired non-pilot
cases whose `let_through` flips. This read-only comparison spends nothing, makes no model call,
and reports the maintainer's fixed paid-run gate and failures without starting a run.

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
moving outputs or deleting derived results cannot regain budget. Four current pilots are
mandatory before b01–b20. A model pilot must have sent a call and
retained decision diagnostics; a zero-call method pilot refuses even at zero cost. Detect-1's
existing tests-only pilot still passes without diagnostics.

Per job, the observed pilot projection is `max(charged_usd, result.cost_usd)`. Alongside it,
the configured worst case is `calls * (input_tokens * input_tariff + completion_limit *
output_tariff) / 1e6`, with no cache discount. `--calls` includes all assumed sends/retries;
input tokens and calls remain explicit assumptions, not provider-enforced ceilings. Admission
uses the larger bound, adds all cumulative historical charges, and checks the remaining jobs
before execution and every attempt. Each send also reserves the configured completion allowance.
Dry-run prints declared, pilot (null if absent), worst-case and admitted per-job costs per arm.
With the illustrative command above, the worst cases are $0 for detect-1, $0.073536 each for
2-v2/3-v2, and $0.155072 for 4-v3; with no history the projection is $6.345024. Forty calls
with those tariffs instead project $31.725120 and refuse the $20 cap. Reduce repetitions
before dropping arms when the projection exceeds the shared cap.

Model-arm configuration changes require **new arm IDs and fresh pilots**; changed configurations
under an existing ID refuse. Roles, rather than literal IDs, select task/diff versus behavior.
The v2 arms replaced detect-2/3/4 because 512 tokens could truncate reasoning and their method
pilots proved no classification. Detect-4-v3 supersedes detect-4-v2: its d02 pilot recorded
`no stable selected difference to classify`, zero tokens and zero cost, then refused because
it sent no model call. Exactly one pilot record per arm is required, so that durable entry
cannot be retried on d01 under the same ID. The new ID changes the arm's prompt-template hash
and configuration fingerprint; all other configuration fields, including the system prompt
hash, stay identical. Reports list detect-4-v2 and the older pilots and their costs as
`superseded`; old records, charges, unknown costs and reservations remain in shared accounting.
Detect-1, detect-2-v2 and detect-3-v2 keep their exact configurations and fingerprints from
main 70d5d50, so their completed pilots are reused. Tests-only retains its v1 identity;
v2-only fields never enter it. The accounting gate still refuses configuration drift before spend.

Each send/retry checks admission against the reservation and remaining shared cap. A pre-send
expiry or cancellation charges nothing and does not block later admissions with unknown billing.
Sends are durable before entering transport; receipts are durable before another send. A request
that may have left without a receipt, or missing usage, stays unknown and stops further work.
Cancellation, unfinished processes or cleanup failure retain the reservation. External estimates
cannot stop a provider from charging more than estimated. Inspect and reconcile unknown billing
outside this runner; it has no reset, force-retry or reconciliation switch.
Transport counting lives in transport.py, outside the arm fingerprint like accounting.py,
so it changes no arm's configuration.

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
