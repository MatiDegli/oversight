# Proof 2: frozen tasks and independent checks

The 16 prompts, kinds, issues, zones, dependencies and parallel pairs in `tasks.json`
freeze the maintainer's approval of 2026-10-30 at 09:32 ART (UTC-3). Paths are relative
to this directory. `pairs` lists both members; p15 depends on p05, and p16 runs alone.
The prompt SHA-256 in `test_proof2.py` was computed from commit `981b1ee`.

Every check, reference and mutant was written and frozen before any Proof 2 run,
without seeing agent output. Checks include independent cases beyond prompt examples
and compatibility observations evaluated on Boltons d31d821d56c8f7756af9c30a864077f2e5855321.
Bug and feature references are minimal single-module patches. Tests-only references
are executable specifications, listed in both `hidden_checks` and `reference`;
the D3 runner evaluates the candidate's tests rather than installing those references.

```sh
python -m tools.eval.proof2 check --task p04 --tree /path/to/candidate
python -m tools.eval.proof2 check --task p10 --tree /path/to/candidate --base /path/to/base
python -m tools.eval.proof2 validate --source /path/to/pinned/boltons-d31d821
```

Use the project's Python for the CLI. Pytest always uses the prepared demo venv's
Python, never an installation of Boltons. Only temporary copies inside this directory
are executed; source trees, Git metadata and environment files are not written.
No model, network client or package installer is invoked. Symlinks in copied trees are refused.
Each subprocess has a timeout and its process group is killed on completion.

For bugs and features, `check` emits JSON counts, duration, failing ids and exit code.
`validate` requires every hidden file to fail on base through a test assertion,
then pass with its reference; the reference must also pass the profile's
`--doctest-modules boltons tests` suite.

For tests-only tasks, `--base` is required; other kinds refuse it. D3 requires:

1. Every production artifact is byte-equal to BASE, including added/deleted paths.
   Comparison excludes only Git metadata; environment files cause a refusal without
   being read. Even artifacts excluded by the execution copy policy are compared.
2. At least one added or changed Python test file under `tests/` exists (pytest's
   `test_*.py` or `*_test.py` names).
3. Those files pass on the candidate, with at least one test executed.
4. The same files fail or error on at least two of three frozen mutants.

The JSON reports every condition, changed production paths, new test files, per-mutant
results and killed ids. Early failures leave later conditions false (not evaluated).
Timeouts, no-test collection and runner failures do not count as kills.
Neither PATH nor BASE is written, even when tests write files.

Each mutant is an appended override in a fresh copy of the candidate's module.
Its first line declares all target names; every declared name must resolve before
anything is appended, otherwise the runner refuses with a reason. Overrides rebind
functions or class attributes, preserving unrelated additions, including p05's
mathutils helpers when p15 runs after p05. Snippets import their own standard-library
dependencies. Required module globals, including constants, belong in `# targets:`:
a missing global refuses that mutant before appending, records `applies=false` and
the reason, and never counts as a kill. Local undefined-name lint/type exemptions
cover these declared module globals.

For tests-only validation, the reference tests must pass on base and kill all three
mutants, and every mutant must apply. The existing Boltons suite is also run against
each mutant and its kills are reported for information only.
Both validation inputs use the execution copy filter, so source caches do not look
like production edits. A refused mutant makes that task's `all_mutants_apply` and
`ok` false; validation continues with the remaining mutants and tasks and exits 1.
Exit 0 means success, 1 a failed condition, and 2 a refusal or infrastructure failure.

On the pin, `OneToOne.update` overwrites value collisions; `OneToOne.unique` raises
`ValueError` for duplicates. p02 preserves and checks both existing behaviors.

## Report

```sh
python -m tools.eval.proof2 report --log RUN.jsonl --results RESULTS.jsonl
python -m tools.eval.proof2 report --log RUN.jsonl --results RESULTS.jsonl --out REPORT.jsonl
```

Use the project's Python, with the backend package importable. The command reads two
local files; it never runs checks or calls a model or network. Each log represents
one run: pilots are separate logs and are never merged into the reported run.
The event reader validates every event; gaps or duplicate sequence numbers refuse
the report. Success exits 0 whatever the measures say. A refusal exits 2 and prints
one `{"error": "..."}` line on stdout, without writing the requested output file.

The operator prepares the results file from `check` output. It is JSON Lines, with
blank lines skipped and these two shapes (the result objects below are examples):

```jsonl
{"kind": "base", "task": "p04", "result": {"exit_code": 1, "passed": 5, "failed": 1, "errors": 0, "failing_test_ids": ["t_new"]}}
{"kind": "revision", "task": "p04", "change_id": "change-1", "revision": 2, "head_commit": "h1b", "result": {"exit_code": 0, "passed": 6, "failed": 0, "errors": 0, "failing_test_ids": []}}
```

Base results apply to bug and feature tasks. Revision results must match the logged
proposal's change id, revision and head commit; tests-only tasks use the full D3
`check` result. Duplicate records, inconsistent tasks, invalid shapes or unmatched
revisions refuse with the results line number. A logged revision without a result
is allowed and has hidden status `unknown`.

Output is deterministic JSON Lines, in this order:

1. `meta`: input hashes, run identity, counts and dated list-price source.
2. `revision`: correctness, hidden status, both regressions and review receipts,
   sorted by numeric change id then revision.
3. `change`: supervision counts, selected revisions, review cost and wall spans,
   sorted by numeric change id.
4. `E1`: first-review agreement with hidden results; duplicate reviews counted apart.
5. `E2`: requests, regression counts and hidden results by revision round.
6. Two `arm` records: `without_oversight` first, then `with_oversight`, with selection
   counts, cost and wall milliseconds per correct change.
7. `run`: observed activity, agent-hours and parallelism, including reviewer intervals.
8. `E3`, `E4`, `E6`, `E7`: each has only `record` and `status: "not_built"`.

E2 findings, arm questions and questions-not-needed, and run parallelism-over-time
also carry `{"status": "not_built"}`. Without Oversight selects the first proposal
with a passing server suite; with Oversight selects the last approved merge.
Wall time starts at the change's first event and ends at that arm's selected event;
the per-correct measure is the floored mean of known spans of correct changes.

Unknown evidence stays unknown: missing hidden checks never imply pass or fail;
regressions need attributable failing ids and their comparison result. Tests-only
regressions are not applicable. Missing token counts or unpriced models give a null
USD total beside a known lower bound. Costs use the stored list prices, never the
logged dollar ledger. A tick without a role also makes the arm without Oversight's
USD total unknown. Plan and lead activity intervals are unavailable; missing
intervals give null totals with known milliseconds preserved. Negative wall spans
are null.
Counts with an `n` gain `pct` only when `n >= 10`, rounded to one decimal place.
Other floats are rounded to six places. JSON keys are sorted, each record ends in
a newline, and no tool execution timestamp or input path is included. `--out`
writes exactly the stdout bytes and prints nothing on success.
