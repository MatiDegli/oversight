# Shortcut detector fix round 5

The comparison pins the corpus to `0df4629` (319 non-merge commits, 158 with
Python changes). Before and after both contain **57 facts and 11 unknowns**.
No (commit, path, kind) identity was added or removed. The closed helper
extraction at `683fe05` remains closed: its visible destination preserves explicit
schema validation (`model_validate`). Helpers replaced by `return None`,
`assert True`, a bare call, or `pass` report facts; invisible or ambiguous imported
targets report unknown with a reason.

Raw scans: `/tmp/shortcut-fix5-before.jsonl` and
`/tmp/shortcut-fix5-after-pinned.jsonl`, with summary sidecars.
Reproduce either scan using the corresponding detector checkout:

```sh
.venv/bin/python -W ignore tools/shortcut_corpus.py . --revision 0df4629 --output /tmp/shortcut-fix5-reproduced.jsonl
```

## Timings

Same input and machine, `timeout=2`: one class, module-level literal CASES,
150/200/590 parametrized methods, with only a trailing comment changed.
Timing is wall time around `detect_shortcuts`; no corpus code is executed.

| Methods | Before (s) | After (s) | Before status | After status |
| ---: | ---: | ---: | --- | --- |
| 150 | 2.042 | 0.166 | unknown (timeout) | no_facts |
| 200 | 2.009 | 0.204 | unknown (timeout) | no_facts |
| 590 | 12.042 | 0.809 | unknown (timeout) | no_facts |

Definition bindings are collected once during source inventory; a cached walk
indexes shadowed table names and checks the deadline at every node. Lookups
check the deadline and use the index. Tests cover interrupted indexing, reuse,
class shadowing, the large classes and both previously uncovered unknown reasons.
The round-4 timeout test uses a nearly zero budget and an upper bound only.

## Corpus by (commit, path)

Each cell lists all finding kinds for that identity. `unknown` is kept separate
from facts, and never represented as an empty result. All identities are shown,
including the unchanged opaque tables: nine of the eleven unknowns are stable
opaque tables reached by unrelated edits, so their rows remain uncomparable.

| Commit | Path | Before kinds | After kinds |
| --- | --- | --- | --- |
| `0e4855c4018eeae2e644b1d129b097603c7b0b53` | `contracts/tests/test_contracts.py` | assertion_loosened, unknown | assertion_loosened, unknown |
| `130e7ad95223c585d8fb7dae759872797741d5c6` | `backend/tests/graph/test_workspace.py` | test_removed | test_removed |
| `14713717b402ee1d333a3cd45944f1e0c46f9ac0` | `tools/tests/test_eslint_boundaries.py` | unknown | unknown |
| `20c0333fb0bcaedb0844f3cf5b6d7cde600e8c70` | `backend/tests/workspace/test_workspace.py` | assertion_loosened | assertion_loosened |
| `23e388110ff5b6f4289cd55c60adc2cf2388aa59` | `backend/tests/graph/test_layout.py` | assertion_loosened, skip_added, test_removed | assertion_loosened, skip_added, test_removed |
| `23e388110ff5b6f4289cd55c60adc2cf2388aa59` | `backend/tests/test_app.py` | test_removed | test_removed |
| `28f08d38f295307c99b6f4b7ae2e6ead4be75923` | `contracts/tests/test_contracts.py` | expected_value_changed, unknown | expected_value_changed, unknown |
| `2b5a9b4e281f47bac0ef98ca13e3891700bdf848` | `backend/tests/workspace/test_runner.py` | assertion_loosened | assertion_loosened |
| `2ff6368407c92538a5c40f11189a64cc1f3fb5f0` | `tools/tests/test_e2e_run.py` | skip_added | skip_added |
| `3841dee9228006bbe3c4cffdd2d607f63de906b1` | `backend/tests/changes/test_user_save.py` | unknown | unknown |
| `38ce742400808473b0fbb73b263b4e612a404e7d` | `tools/tests/test_smoke.py` | assertion_loosened | assertion_loosened |
| `407515a0082b1668ab2649e8af46e4411d2291c2` | `backend/tests/changes/test_request_changes.py` | assertion_loosened | assertion_loosened |
| `5190fc7e9d8d26365fa03c305252b2b2817f001d` | `tools/tests/test_demo_check.py` | stub_left | stub_left |
| `51b980321c95e5c5a34b55fe0bcb3646a7523136` | `contracts/tests/test_contracts.py` | test_removed, unknown | test_removed, unknown |
| `6636a0509aeb09814115784d9fc9fba08518e69d` | `backend/tests/changes/test_shortcut_detector_fix2.py` | assertion_loosened | assertion_loosened |
| `683fe05c8698ed428b2299f114e8eaae59b3f866` | `backend/tests/api/test_brake.py` | assertion_loosened | assertion_loosened |
| `683fe05c8698ed428b2299f114e8eaae59b3f866` | `backend/tests/api/test_start.py` | assertion_loosened | assertion_loosened |
| `6ba86744b165ad11204e48f144197a6d37aa6a8c` | `tools/tests/test_memwatch.py` | assertion_loosened | assertion_loosened |
| `6f05291b46a57cefa27e39fb9fd6e3b1cfebd46a` | `tools/tests/test_memwatch.py` | assertion_loosened, test_removed | assertion_loosened, test_removed |
| `7e00c5158d1248d5f5031d7f36ffeb5276742cb6` | `backend/tests/api/test_commands.py` | assertion_loosened | assertion_loosened |
| `812ab678dfe28481626a423331db0097495ff9e2` | `tools/tests/test_check_commits.py` | assertion_loosened | assertion_loosened |
| `835e90958a996e7bf1532552b95c4e12db307907` | `tools/tests/test_capped.py` | assertion_loosened | assertion_loosened |
| `846d1936ec117ac91a0d5a082d2f32c9fa4829c9` | `backend/tests/events/test_fixtures.py` | assertion_loosened, test_removed, unknown | assertion_loosened, test_removed, unknown |
| `8e3219ab9a21f4c88b29c0d43e39daa65f3bc192` | `tools/tests/test_shortcut_corpus.py` | skip_added | skip_added |
| `8ed1869fa734d4ab8cd01b99a10fd78da6e64035` | `tools/eval/tests/test_lead_probe.py` | assertion_loosened, unknown | assertion_loosened, unknown |
| `912a6b80a308a343115fb76394fe7c59acb28d4a` | `backend/tests/agents/test_loop_sink.py` | assertion_loosened, expected_value_changed | assertion_loosened, expected_value_changed |
| `932e1c2ff9887da2d0d787257aa5ecd839fcfc10` | `backend/tests/api/test_slots.py` | test_removed | test_removed |
| `942345911bb9c37e58282b2e0d0c506238bfce03` | `backend/tests/agents/test_client.py` | assertion_loosened | assertion_loosened |
| `942345911bb9c37e58282b2e0d0c506238bfce03` | `backend/tests/agents/test_loop.py` | assertion_loosened | assertion_loosened |
| `942345911bb9c37e58282b2e0d0c506238bfce03` | `backend/tests/api/test_commands.py` | assertion_loosened | assertion_loosened |
| `942345911bb9c37e58282b2e0d0c506238bfce03` | `tools/tests/test_probe_api.py` | assertion_loosened | assertion_loosened |
| `99f9e5982b725f827422950ed8a542270a19c5a5` | `backend/tests/api/test_setup.py` | expected_value_changed | expected_value_changed |
| `99f9e5982b725f827422950ed8a542270a19c5a5` | `backend/tests/events/test_store.py` | assertion_loosened | assertion_loosened |
| `a464e7047415a753542a8cfe5479880529cfa13b` | `backend/tests/agents/test_tools.py` | assertion_loosened | assertion_loosened |
| `a5f0583e8b8c73c16585304709fd200442092b82` | `tools/tests/test_e2e_run.py` | skip_added | skip_added |
| `a605a12fca1e11ddbcf33e34d0a98329b3d9efd7` | `tools/tests/test_check_commits.py` | assertion_loosened | assertion_loosened |
| `a6765c87e5a31b7a758775198671ce3e02ba3247` | `backend/tests/agents/test_client.py` | expected_value_changed | expected_value_changed |
| `aa0b401009189482d3c266ed667fd8524dd9ace5` | `tools/tests/test_demo_check.py` | assertion_loosened, stub_left, test_removed | assertion_loosened, stub_left, test_removed |
| `ae67995ada3f40b93f3232b01bd0dfb708a2950a` | `tools/tests/test_e2e_run.py` | test_removed | test_removed |
| `aedf4c45d6ec64fce998ef80d6724145e0b6e859` | `backend/tests/api/test_brake.py` | assertion_loosened | assertion_loosened |
| `aedf4c45d6ec64fce998ef80d6724145e0b6e859` | `backend/tests/api/test_commands.py` | test_removed | test_removed |
| `aedf4c45d6ec64fce998ef80d6724145e0b6e859` | `backend/tests/api/test_slots.py` | assertion_loosened | assertion_loosened |
| `aedf4c45d6ec64fce998ef80d6724145e0b6e859` | `contracts/tests/test_contracts.py` | assertion_loosened, expected_value_changed, unknown | assertion_loosened, expected_value_changed, unknown |
| `bc8abbe5a732c3055be9a836ceb2703a86cd0259` | `backend/tests/changes/test_shortcut_detector_fix2.py` | assertion_loosened | assertion_loosened |
| `bc8abbe5a732c3055be9a836ceb2703a86cd0259` | `backend/tests/changes/test_shortcut_detector_round1.py` | assertion_loosened | assertion_loosened |
| `c816df0fbbdd117010f5bf1dff43963538dcf007` | `backend/tests/test_app.py` | expected_value_changed | expected_value_changed |
| `d0b0a51368d39a7a68b97f59b4b135710efb2793` | `backend/tests/e2e/test_two_agents.py` | assertion_loosened | assertion_loosened |
| `d0cb438b527e4d78a4e44fbc828558b2c5fc8844` | `backend/tests/events/test_fixtures.py` | unknown | unknown |
| `d7b5b163053f8c26d9383e196c5a9fee8c2724db` | `backend/tests/test_app.py` | assertion_loosened | assertion_loosened |
| `d7b5b163053f8c26d9383e196c5a9fee8c2724db` | `tools/tests/test_smoke.py` | test_removed | test_removed |
| `d94f4cdfec4be5093e58a7f7b362aecf893b90ce` | `tools/tests/test_capped.py` | assertion_loosened | assertion_loosened |
| `db0de4a250640fec8c468a2eb75d44388958d31f` | `tools/tests/test_probe_api.py` | test_removed | test_removed |
| `f431c99efbf2d230a170b60718c23b2d21467273` | `contracts/tests/test_reserved.py` | unknown | unknown |
| `ff613d05059ec1a08a80ba6086d2eeb5866be14a` | `backend/tests/changes/test_beforeafter.py` | unknown | unknown |

## Known limits deferred by request

- Imported parameter tables changed only in their defining module, fixture params,
  and `metafunc.parametrize` are not compared (NIT 2).
- Dead-branch assertions such as `if 0`, `while False`, or `for _ in []`,
  class-qualified helper calls, aliased skip marks, and the misleading rename
  wording for non-collected destinations remain deferred (NIT 5).
- Helper check recognition is syntactic: assertion calls and explicit schema
  validation names are recognized; arbitrary helper/dataflow equivalence is not.
