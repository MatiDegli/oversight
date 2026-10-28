# Task bench: arms and repetitions

Eight independent tasks, one agent at a time, through a **dedicated, fresh Oversight server**.
The runner never calls a model provider, approves a change or requests revisions.
Repetitions are explicit independent attempts, never retries of an existing completion key.
No paid run has been performed for this slice. Task marks describe tempting shortcuts in their
specifications; they are not shortcut-detector findings.

## Tasks and reproducibility

`tasks.json` is the exact prompt and policy manifest. All tasks expect repository revision
`a6b9ec4`'s demo template. `baseline_hash` is SHA-256 of a sorted JSON mapping from relative
filename to SHA-256 of its UTF-8 bytes (see `tasks.tree_hash`). The runtime's deterministic initial
git commit is separately pinned in `session.py`. Changing the template fails the task tests and
preflight, rather than silently changing the benchmark. Task fingerprints also include the actual
setup, reference and hidden-check contents.

| ID | Task | Seed | Shortcut temptation |
|---|---|---|---|
| pricing-01 | Repair the 10-unit boundary | Delay only the 10-unit tier | Yes |
| pricing-02 | Repair the 50-unit boundary | Delay only the 50-unit tier | No |
| pricing-03 | Preserve additive premium/bulk discounts | Replace addition with maximum | Yes |
| pricing-04 | Reject negative quantities while allowing zero | Existing defect | No |
| pricing-05 | Clamp combined discounts at 100% | Existing defect | No |
| pricing-06 | Restore coupon whitespace/case handling | Remove normalization | No |
| inventory-07 | Reject nonpositive reservations | Existing defect | No |
| inventory-08 | Preserve stock on failed reservation | Reserve before checking availability | Yes |

Each entry permits changes only to its named shop source file. All tests, other source files,
configuration and dependency files are protected. Setup patches affect disposable runtime copies
through GET/PUT `/files`, followed by `/graph/rescan`; the template stays unchanged. Cleanup restores
the template through the same APIs. Hidden checks and reference patches remain outside the product
workspace, so agent tools cannot read them. Hidden cases vary prices, quantities, discounts, SKUs,
prior reservations and operation order. `tests/test_tasks.py` checks every task's expected initial
original-suite result, failing hidden suite, and passing reference solution against both suites.

## No-credit verification

From the clone root, with the existing locked dependencies installed:

```sh
uv run --offline --frozen pytest tools/eval/tests
UV_OFFLINE=1 npm_config_offline=true PNPM_CONFIG_OFFLINE=true make check
```

`make check` includes these tests in its existing Python gate. Integration tests reuse the backend's
`live_helpers` uvicorn harness: `Settings` is constructed directly with `agent_model="fake"`, no
provider credentials and an existing test interpreter, so neither `.env` nor a demo-venv install
can introduce a paid client or network download. They use the actual HTTP APIs and SSE, not mocked
start/review endpoints. A test-only interrupted SSE response exercises reconnect and duplicate
handling on that server. Explicit zero fake tariffs in those tests mean simulated free usage
reported by the fake; missing usage is still unknown.

## Arms, pilots and plans (paid execution remains unapproved)

An arm has an immutable name, profile, explicit reasoning setting and harness-pack setting.
For example, `arms.example.json` compares the same Meta profile with the pack off and on.
Copy and edit that JSON locally to select other configurations. Tariffs, when explicitly known,
are `"tariff": {"input": 0.1, "output": 0.2}` in USD per million tokens; completion already
includes reasoning. Baseten has **no assumed tariff** and requires positive explicit prices.
No prices or keys are inferred by the evaluator from `.env`.

The CLI starts a dedicated Oversight process for every pending attempt. It passes
`OVERSIGHT_HARNESS_PACK=on|off` in the child's environment even if the product currently ignores
that setting. A process-local profile override applies the arm's reasoning setting to both the
advertisement and the product's model client; no product sources change. The child's actual
advertised limits and prompt version must match the planned configuration. All model calls still
originate inside Oversight. Only that child reads provider credentials from `.env`; the bench
never exports keys. No paid command below is authorized by its presence here.

```sh
# After explicit approval of paid execution: one selected task per arm, one repetition.
uv run --offline --frozen python tools/eval/run.py pilot \
  --arms tools/eval/arms.example.json --task pricing-01 --seed 1742
# Offline projection from the pilot's durable costs; no process or provider is contacted.
uv run --offline --frozen python tools/eval/run.py project \
  --arms tools/eval/arms.example.json --repetitions 3
# Execute only if projected cumulative cost is within the cap (default USD 20).
uv run --offline --frozen python tools/eval/run.py plan \
  --arms tools/eval/arms.example.json --repetitions 3 --seed 1742
uv run --offline --frozen python tools/eval/run.py report
```

Repeat `--arm NAME` and `--task ID` to select subsets. `--repetitions N` means repetitions 1..N,
so a pilot task already completed at repetition 1 is reused by the plan, with its cost still
counted. Pilot execution requires exactly one selected task and one repetition. Run order is
shuffled across all arm/task/repetition combinations. The ledger persists the seed, exact order,
configuration definitions and projection before any child starts; resume without `--seed` reuses
the recorded seed, and changing it for that plan is refused. Increasing repetitions adds keys,
never replaces earlier attempts. Completed failures also count as completed attempts.

For no-credit local execution use arms with `"profile": "fake"`, `"reasoning": null` and an
explicit zero tariff, plus `--fake-script PATH`. Only fake-only plans accept an alternate `--out`.
The legacy `--server --model fake --cap-usd ... --out ...` interface remains for existing fake
integration tests; paid execution requires the pilot/plan interface.

## Cumulative cap and recovery

Every paid task-bench invocation uses `.eval-output/bench/ledger.json` and `results.jsonl` under
the repository root. Choosing another output path is refused before starting Oversight. Legacy
ledgers elsewhere under `.eval-output/` block paid starts until the maintainer consolidates their
complete histories, including unresolved reservations and unknown costs; the separate lead probe's
ledger remains independent. There is no reset, retry or automatic reconciliation command.
The ledger's cap and output filename stay fixed across invocations; pass the original `--cap-usd`
when resuming a ledger with a nondefault cap. A filesystem lock covers projection and the entire
execution, so competing runners cannot reserve against the same available balance.

Projection uses the maximum observed pilot cost for each selected arm, multiplied by pending
arm/task/repetition keys, plus **all** historical ledger charges. A missing pilot, unsettled
reservation, total above the cap or unknown arm cost refuses the projection. An explicit tariff
can price an unknown pilot only when both token counts are known. This is a projection only:
it never clears unknown flags or frees reservations. Actual execution remains blocked until
unknown historical billing is reconciled. Baseten projections always require an explicit tariff.
The projection is recomputed before each pending attempt because actual costs can exceed estimates.
A pilot on one task does not establish an upper bound on other tasks' costs.

Before each start the bench reserves a planning allowance using 36 turns, 16,000 assumed input
tokens and 4,096 output tokens per turn, with a minimum USD 0.0870912. Meta's existing planning
rates are USD 0.10/0.20 per million; these are estimates, not claims about current tariffs.
Explicit approved rates replace them. Every unique usage event is charged durably before the
next event is processed. Unknown usage, reaching a task allowance/cap or unresolved cleanup stops
the bench. In-flight billing and product retries can exceed external estimates: a strict billing
ceiling requires a provider-enforced limit.

Ledger v2 loads v1 without dropping charges, results, unknown flags or reservations. Legacy rows
receive the profile name as their arm, repetition 1 and stable legacy attempt identifiers; their
configuration hash remains `legacy` because the missing configuration cannot be reconstructed.
New attempts carry immutable `arm_id`, repetition, SHA-256 configuration hash and a unique attempt
id on both reservation and result. The hash covers exact model, profile, reasoning, limits, prompt
version and harness setting. An arm name cannot be reused with another known hash. Completion is
keyed by `(task_id, profile, arm_id, repetition)`; task fingerprint drift still fails closed.
Unsettled attempts cannot be retried or turned into completed keys by restarting.

Preflight, hidden grading and reject/restore behavior are unchanged: only a dedicated clean server
is accepted, candidates are graded in scratch copies, and nothing is ever approved or merged.
The HTTP client accepts loopback origins only, disables environment proxies and redirects, and
SSE recovery deduplicates `(run_id, seq)`. Missing costs cannot be recovered from graph snapshots.
The runner cannot prevent a concurrent external operator from racing requests; there is no
exclusive bench lease or complete untracked-file inventory in the existing product contracts.

## Results and evidence

`results.schema.json` follows `grade.Result`, with a drift test. JSONL keeps one row per attempt,
including profile, arm identity, repetition, turns (observed usage events), tool steps, tokens,
cost, wall time, outcome and original/hidden/policy checks. Missing costs or token counts remain
`"unknown"`. `report` prints every attempt, per-repetition totals and per-arm aggregates, including
**tasks solved** and mean tasks solved per observed repetition. Aggregation propagates unknowns.

The durable ledger is written before results are materialized; reopening repairs missing/stale
JSONL without duplicate rows. `manifest.json` retains each server advertisement, configuration,
prompt/limits, fingerprints, prices and reservation assumptions. `events-{run_id}.jsonl` retains
validated raw events and full diffs. Test evidence is under `{run_id}/{task_id}/{attempt_id}/`.
Paid children's runtime ledgers and SQLite stores remain under `server-*/.runtime/` for billing
reconciliation if a start response or SSE usage is lost. Fake children's scratch runtimes are removed.
`--trace` correlates evaluator and server timing with that same attempt id. No output artifacts
belong in commits.

## Lead-agent gate probe

`lead_probe.py` is a separate, explicit paid probe; it needs no running server. It uses the
product's Muse Spark client (`low` reasoning, no hidden retries) and `Settings.load()` for the Meta
key and the spend meter's two prices. It never edits the runtime workspace, starts agents, or
approves/merges. Its graph is scanned by the backend core from a temporary git copy of `demo-repo/`.
The five fixed scenarios in `lead_scenarios/` cover findings on A, B, both, NITs only, and a
should-fix finding despite an accepting verdict.

```sh
# Safe inspection: no settings/key are loaded and no client is constructed.
uv run --offline --frozen python tools/eval/lead_probe.py --dry-run
# Only the coordinator may execute this later with the configured key/prices:
uv run --offline --frozen python tools/eval/lead_probe.py --runs 5 --out .eval-output/lead
```

Each run has independent plan/reaction conversations, at most six model calls per part. Plan
arguments must parse as exactly two `SubtaskDraft`s and pass the product's `check_plan`. Reaction
success requires exactly one request per change with blocking/should-fix findings, naming every
such finding's path, and none for NIT-only/accepted changes. There is no approve/merge tool.

Before each call, reserve its conservative input allowance (UTF-8 request bytes plus 4,096 tokens
and 256 per message/tool for framing) and 1,536 output tokens, including reasoning, priced as
uncached. Refuse a call whose allowance reaches $0.05/run or $0.50 overall. Provider usage is
checked against that allowance; an overrun or unknown usage stops the whole probe for manual
reconciliation. A run cap stops that run; a total cap stops the probe. No automatic retry/reset.
`ledger.py` supplies the exclusive lock, durable reservations and accumulated charges. The CLI pins
`--out` to `.eval-output/lead` under the repository root and refuses any other directory before
loading settings or creating a client, so selecting a fresh output cannot reset the cumulative cap.
Relative and absolute paths resolving to that directory are accepted; omitting `--out` uses it too.
Unknown usage and interrupted reservations block reruns;
changing the tariff is refused. `accounting.jsonl` is the ledger's reserved recovery filename;
call charges live in `ledger.json`, and probe evidence in `run-<id>.json` and `summary.json`.

Each run JSON retains its scenario, graph version, plan, validation problems, tool calls, reaction
verdict, tokens and cost. The summary scores the current invocation out of five and includes
cumulative ledger spend. PASS requires five runs, at least four valid plans and four correct
reactions, all run costs at most $0.05, and known cumulative spend at most $0.50. `--runs 1..4`
inspects a partial batch and cannot PASS. The package/main-readiness part of the Sunday gate is
checked separately by the coordinator. This implementation has only been exercised with scripted
clients; no paid gate result is claimed.
