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
mathutils helpers when p15 runs after p05. The snippets rely on their target module's
globals, hence their local undefined-name lint/type exemptions.

For tests-only validation, the reference tests must pass on base and kill all three
mutants, and every mutant must apply. The existing Boltons suite is also run against
each mutant and its kills are reported for information only.
Exit 0 means success, 1 a failed condition, and 2 a refusal or infrastructure failure.

On the pin, `OneToOne.update` overwrites value collisions; `OneToOne.unique` raises
`ValueError` for duplicates. p02 preserves and checks both existing behaviors.
