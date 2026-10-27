# Shortcut detector fix round 6

## Scope and limits

The three SHOULD-FIX findings and NITs 1, 3 and 4 from re-review 5 are addressed.
The module-to-source suffix index is built once per analysis. Import lookup uses
that index, drops modules without the requested definition or imported binding,
and treats visible re-exports as opaque. The helper walk and index loops check
the shared analysis deadline. An opaque import produces a helper-removal unknown
only when the old helper has no matched destination; a helper still defined at
its original identity does not make unrelated subprocess.run/os.path.join calls
unknown. An opaque import cannot be verified by an unrelated same-bodied helper.

Schema validation alone does not classify a function as an asserting helper.
The already closed extraction case remains supported: an originally asserting
helper may retain the same explicit validation calls after extraction. Replacing
an unrelated assertion with model_validate still reports a fact. All prior
regressions remain in the detector suite.

NIT 2 remains a declared limit: raise AssertionError, pytest.fail and delegation
to another asserting function do not themselves classify a helper as asserting.
The detector remains syntactic, cooperative and unwired to the change manager.

## Deadline evidence

The old near-zero timeout test has been replaced with clocks returning integer
read counts. Sources are parsed before patching the clock; expiry follows several
successful reads inside the tested loop. Matching and pairing passes are isolated
so a later check cannot hide the removal of the tested check. The table lookup
test warms the cached index before advancing the clock.

Each check below was actually replaced with `pass`, one at a time, and restored
in a finally block. Each named test failed (pytest exit 1); 18/18 experiments
killed the mutation, covering 17 distinct check sites. `within_deadline` was
removed twice to verify its row and assertion callers independently.
Paths below are relative to backend/src/oversight/changes; tests are relative to
backend/tests/changes. The `#` identifies a function's check in source order.

| Removed check | Failing test | Exit |
| --- | --- | ---: |
| `_shortcuts_tests.py:319 _collect_parametrize #1` | `test_shortcut_review_round4.py::test_row_loops_cross_deadline_after_reads[collect]` | 1 |
| `_shortcuts_tests.py:386 _compare_rows #1` | `test_shortcut_review_round4.py::test_row_loops_cross_deadline_after_reads[pair]` | 1 |
| `_shortcuts_tests.py:401 _compare_rows #2` | `test_shortcut_review_round4.py::test_row_loops_cross_deadline_after_reads[pair]` | 1 |
| `_shortcuts_common.py:321 within_deadline #1` | `test_shortcut_review_round4.py::test_row_loops_cross_deadline_after_reads[inner]` | 1 |
| `_shortcuts_asserts.py:221 _unmatched_assertions #1` | `test_shortcut_review_round4.py::test_assertion_matching_crosses_deadline_after_reads[outer]` | 1 |
| `_shortcuts_asserts.py:225 _unmatched_assertions #2` | `test_shortcut_review_round4.py::test_assertion_matching_crosses_deadline_after_reads[inner]` | 1 |
| `_shortcuts_asserts.py:251 _compare_assertions #1` | `test_shortcut_review_round4.py::test_assertion_comparison_crosses_deadline_after_reads[outer]` | 1 |
| `_shortcuts_asserts.py:266 _compare_assertions #2` | `test_shortcut_review_round4.py::test_assertion_comparison_crosses_deadline_after_reads[forward]` | 1 |
| `_shortcuts_asserts.py:290 _compare_assertions #3` | `test_shortcut_review_round4.py::test_assertion_comparison_crosses_deadline_after_reads[reverse]` | 1 |
| `_shortcuts_common.py:321 within_deadline #1` | `test_shortcut_review_round4.py::test_assertion_comparison_crosses_deadline_after_reads[equivalent]` | 1 |
| `_shortcuts_asserts.py:342 _compare_raises #1` | `test_shortcut_review_round4.py::test_raises_comparison_crosses_deadline_after_reads[outer]` | 1 |
| `_shortcuts_asserts.py:345 _compare_raises #2` | `test_shortcut_review_round4.py::test_raises_comparison_crosses_deadline_after_reads[inner]` | 1 |
| `_shortcuts_helpers.py:124 _imported_calls #1` | `test_shortcut_review_round6.py::test_helper_calls_cross_deadline_inside_walk` | 1 |
| `_shortcuts_helpers.py:79 _module_index #1` | `test_shortcut_review_round6.py::test_module_index_crosses_deadline_inside_build` | 1 |
| `_shortcuts_helpers.py:100 _imported_target #1` | `test_shortcut_review_round6.py::test_import_lookup_crosses_deadline_inside_index[splits]` | 1 |
| `_shortcuts_helpers.py:102 _imported_target #2` | `test_shortcut_review_round6.py::test_import_lookup_crosses_deadline_inside_index[candidates]` | 1 |
| `_shortcuts_common.py:269 table_shadows #1` | `test_shortcut_review_round5.py::test_table_index_checks_deadline_during_the_walk` | 1 |
| `_shortcuts_common.py:283 table_value #1` | `test_shortcut_review_round5.py::test_table_index_observes_the_analysis_deadline` | 1 |

Full temporary logs: /tmp/shortcut-fix6-mutation-{1..18}.log, with the table in
/tmp/shortcut-fix6-mutations.json. Each baseline test passes with checks restored.

The same 20,000 imported check() calls and 400 changed Python files, below the
2 MB source cap, were analyzed with timeout=2 on this machine. The baseline
8c355ed took **16.843 s**; the fixed detector took **2.059 s**. Both returned
unknown with an explicit analysis-timeout reason. The regression test requires
unknown and elapsed <3 s; the index-build test also requires exactly one build.
This is a cooperative bound, not preemption of parsing or OS scheduling.

## Pinned corpus comparison by (commit, path)

Both scans use the same repository history through 8c355ed: **320 non-merge
commits, 159 with Python changes; 58 facts and 11 unknowns before and after**.
Facts exclude unknown and parse_error (there are no parse errors in this scan).
There are **no changed (commit, path) identities**, no changed kinds/multiplicities
at an identity, and no added or removed identities. The earlier 319-commit cohort
through 0df4629 remains 57 facts + 11 unknowns; 8c355ed contributes one existing
assertion_loosened fact at backend/tests/changes/test_shortcut_review_round4.py.
The closed 683fe05 helper extraction has no added finding.

Raw scans and summary sidecars: /tmp/shortcut-fix6-before.jsonl and
/tmp/shortcut-fix6-after.jsonl. Reproduce with the baseline or fixed detector:

```sh
.venv/bin/python -W ignore tools/shortcut_corpus.py . --revision 8c355ed --output /tmp/shortcut-fix6-reproduced.jsonl
```

Each row counts all findings for that exact commit/path identity. Unlisted
identities have zero facts and zero unknowns in both scans.

| Commit | Path | Facts before | Facts after | Unknowns before | Unknowns after |
| --- | --- | ---: | ---: | ---: | ---: |
| `0e4855c4018eeae2e644b1d129b097603c7b0b53` | `contracts/tests/test_contracts.py` | 1 | 1 | 1 | 1 |
| `130e7ad95223c585d8fb7dae759872797741d5c6` | `backend/tests/graph/test_workspace.py` | 1 | 1 | 0 | 0 |
| `14713717b402ee1d333a3cd45944f1e0c46f9ac0` | `tools/tests/test_eslint_boundaries.py` | 0 | 0 | 1 | 1 |
| `20c0333fb0bcaedb0844f3cf5b6d7cde600e8c70` | `backend/tests/workspace/test_workspace.py` | 1 | 1 | 0 | 0 |
| `23e388110ff5b6f4289cd55c60adc2cf2388aa59` | `backend/tests/graph/test_layout.py` | 3 | 3 | 0 | 0 |
| `23e388110ff5b6f4289cd55c60adc2cf2388aa59` | `backend/tests/test_app.py` | 1 | 1 | 0 | 0 |
| `28f08d38f295307c99b6f4b7ae2e6ead4be75923` | `contracts/tests/test_contracts.py` | 1 | 1 | 1 | 1 |
| `2b5a9b4e281f47bac0ef98ca13e3891700bdf848` | `backend/tests/workspace/test_runner.py` | 1 | 1 | 0 | 0 |
| `2ff6368407c92538a5c40f11189a64cc1f3fb5f0` | `tools/tests/test_e2e_run.py` | 1 | 1 | 0 | 0 |
| `3841dee9228006bbe3c4cffdd2d607f63de906b1` | `backend/tests/changes/test_user_save.py` | 0 | 0 | 1 | 1 |
| `38ce742400808473b0fbb73b263b4e612a404e7d` | `tools/tests/test_smoke.py` | 1 | 1 | 0 | 0 |
| `407515a0082b1668ab2649e8af46e4411d2291c2` | `backend/tests/changes/test_request_changes.py` | 1 | 1 | 0 | 0 |
| `5190fc7e9d8d26365fa03c305252b2b2817f001d` | `tools/tests/test_demo_check.py` | 1 | 1 | 0 | 0 |
| `51b980321c95e5c5a34b55fe0bcb3646a7523136` | `contracts/tests/test_contracts.py` | 1 | 1 | 1 | 1 |
| `6636a0509aeb09814115784d9fc9fba08518e69d` | `backend/tests/changes/test_shortcut_detector_fix2.py` | 1 | 1 | 0 | 0 |
| `683fe05c8698ed428b2299f114e8eaae59b3f866` | `backend/tests/api/test_brake.py` | 1 | 1 | 0 | 0 |
| `683fe05c8698ed428b2299f114e8eaae59b3f866` | `backend/tests/api/test_start.py` | 1 | 1 | 0 | 0 |
| `6ba86744b165ad11204e48f144197a6d37aa6a8c` | `tools/tests/test_memwatch.py` | 1 | 1 | 0 | 0 |
| `6f05291b46a57cefa27e39fb9fd6e3b1cfebd46a` | `tools/tests/test_memwatch.py` | 2 | 2 | 0 | 0 |
| `7e00c5158d1248d5f5031d7f36ffeb5276742cb6` | `backend/tests/api/test_commands.py` | 1 | 1 | 0 | 0 |
| `812ab678dfe28481626a423331db0097495ff9e2` | `tools/tests/test_check_commits.py` | 1 | 1 | 0 | 0 |
| `835e90958a996e7bf1532552b95c4e12db307907` | `tools/tests/test_capped.py` | 1 | 1 | 0 | 0 |
| `846d1936ec117ac91a0d5a082d2f32c9fa4829c9` | `backend/tests/events/test_fixtures.py` | 2 | 2 | 1 | 1 |
| `8c355edc8045c9849e542e8e9c6a9d8fc8942947` | `backend/tests/changes/test_shortcut_review_round4.py` | 1 | 1 | 0 | 0 |
| `8e3219ab9a21f4c88b29c0d43e39daa65f3bc192` | `tools/tests/test_shortcut_corpus.py` | 1 | 1 | 0 | 0 |
| `8ed1869fa734d4ab8cd01b99a10fd78da6e64035` | `tools/eval/tests/test_lead_probe.py` | 1 | 1 | 1 | 1 |
| `912a6b80a308a343115fb76394fe7c59acb28d4a` | `backend/tests/agents/test_loop_sink.py` | 2 | 2 | 0 | 0 |
| `932e1c2ff9887da2d0d787257aa5ecd839fcfc10` | `backend/tests/api/test_slots.py` | 1 | 1 | 0 | 0 |
| `942345911bb9c37e58282b2e0d0c506238bfce03` | `backend/tests/agents/test_client.py` | 1 | 1 | 0 | 0 |
| `942345911bb9c37e58282b2e0d0c506238bfce03` | `backend/tests/agents/test_loop.py` | 1 | 1 | 0 | 0 |
| `942345911bb9c37e58282b2e0d0c506238bfce03` | `backend/tests/api/test_commands.py` | 1 | 1 | 0 | 0 |
| `942345911bb9c37e58282b2e0d0c506238bfce03` | `tools/tests/test_probe_api.py` | 1 | 1 | 0 | 0 |
| `99f9e5982b725f827422950ed8a542270a19c5a5` | `backend/tests/api/test_setup.py` | 1 | 1 | 0 | 0 |
| `99f9e5982b725f827422950ed8a542270a19c5a5` | `backend/tests/events/test_store.py` | 1 | 1 | 0 | 0 |
| `a464e7047415a753542a8cfe5479880529cfa13b` | `backend/tests/agents/test_tools.py` | 1 | 1 | 0 | 0 |
| `a5f0583e8b8c73c16585304709fd200442092b82` | `tools/tests/test_e2e_run.py` | 1 | 1 | 0 | 0 |
| `a605a12fca1e11ddbcf33e34d0a98329b3d9efd7` | `tools/tests/test_check_commits.py` | 1 | 1 | 0 | 0 |
| `a6765c87e5a31b7a758775198671ce3e02ba3247` | `backend/tests/agents/test_client.py` | 1 | 1 | 0 | 0 |
| `aa0b401009189482d3c266ed667fd8524dd9ace5` | `tools/tests/test_demo_check.py` | 3 | 3 | 0 | 0 |
| `ae67995ada3f40b93f3232b01bd0dfb708a2950a` | `tools/tests/test_e2e_run.py` | 1 | 1 | 0 | 0 |
| `aedf4c45d6ec64fce998ef80d6724145e0b6e859` | `backend/tests/api/test_brake.py` | 1 | 1 | 0 | 0 |
| `aedf4c45d6ec64fce998ef80d6724145e0b6e859` | `backend/tests/api/test_commands.py` | 1 | 1 | 0 | 0 |
| `aedf4c45d6ec64fce998ef80d6724145e0b6e859` | `backend/tests/api/test_slots.py` | 1 | 1 | 0 | 0 |
| `aedf4c45d6ec64fce998ef80d6724145e0b6e859` | `contracts/tests/test_contracts.py` | 2 | 2 | 1 | 1 |
| `bc8abbe5a732c3055be9a836ceb2703a86cd0259` | `backend/tests/changes/test_shortcut_detector_fix2.py` | 1 | 1 | 0 | 0 |
| `bc8abbe5a732c3055be9a836ceb2703a86cd0259` | `backend/tests/changes/test_shortcut_detector_round1.py` | 1 | 1 | 0 | 0 |
| `c816df0fbbdd117010f5bf1dff43963538dcf007` | `backend/tests/test_app.py` | 1 | 1 | 0 | 0 |
| `d0b0a51368d39a7a68b97f59b4b135710efb2793` | `backend/tests/e2e/test_two_agents.py` | 1 | 1 | 0 | 0 |
| `d0cb438b527e4d78a4e44fbc828558b2c5fc8844` | `backend/tests/events/test_fixtures.py` | 0 | 0 | 1 | 1 |
| `d7b5b163053f8c26d9383e196c5a9fee8c2724db` | `backend/tests/test_app.py` | 1 | 1 | 0 | 0 |
| `d7b5b163053f8c26d9383e196c5a9fee8c2724db` | `tools/tests/test_smoke.py` | 1 | 1 | 0 | 0 |
| `d94f4cdfec4be5093e58a7f7b362aecf893b90ce` | `tools/tests/test_capped.py` | 1 | 1 | 0 | 0 |
| `db0de4a250640fec8c468a2eb75d44388958d31f` | `tools/tests/test_probe_api.py` | 1 | 1 | 0 | 0 |
| `f431c99efbf2d230a170b60718c23b2d21467273` | `contracts/tests/test_reserved.py` | 0 | 0 | 1 | 1 |
| `ff613d05059ec1a08a80ba6086d2eeb5866be14a` | `backend/tests/changes/test_beforeafter.py` | 0 | 0 | 1 | 1 |
