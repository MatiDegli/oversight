# Proof 2: frozen tasks and feature checks

The 16 prompts, kinds, issues, zones, dependencies and parallel pairs in `tasks.json`
freeze the maintainer's approval of 2026-10-30 at 09:32 ART (UTC-3). Paths are relative
to this directory. `pairs` lists both members; p15 depends on p05, and p16 runs alone.
The second front fills the five bug and four tests-only checks, references and mutants;
its empty check lists currently cause a refusal. Prompts must remain unchanged.

The seven feature checks and minimal, single-module reference patches were frozen
on 2026-10-30, before any Proof 2 run, without seeing agent output. Checks include independent cases
beyond prompt examples and compatibility observations evaluated on the pinned base.
The source is Boltons d31d821d56c8f7756af9c30a864077f2e5855321.

```sh
python -m tools.eval.proof2 check --task p04 --tree /path/to/candidate
python -m tools.eval.proof2 validate --source /path/to/pinned/boltons-d31d821
```

Use the project's Python for the CLI. Pytest always uses the prepared demo venv's
Python, never an installation of Boltons. Only temporary copies inside this directory
are executed; source trees, Git metadata and environment files are not written.
No model, network client or package installer is invoked. Symlinks in copied trees are refused.
Each subprocess has a timeout and its process group is killed on completion.

`check` emits JSON counts, duration, failing ids and exit code. `validate` emits one
JSON line per populated task: every hidden file must fail on base through a test
assertion, then pass with its reference; the reference must also pass the profile's
`--doctest-modules boltons tests` suite. Exit 0 means success, 1 means a failed
condition, and 2 means a refused input or infrastructure failure.
