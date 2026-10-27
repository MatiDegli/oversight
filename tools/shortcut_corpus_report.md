# Shortcut detector hardening

Initial hardening baseline: `f377dd45648e0e45d85af6afd112aca8e12a3ccc`.
The hardening and precision sections are historical; round 7 below is current.
The detector remains pure, deterministic, stdlib only, and exported by
`oversight.changes.detect_shortcuts`. No contracts or product integration changed.

## Corpus and method

The complete non-merge ancestry of each local HEAD was scanned, including root
commits and commits without Python changes. Old/new Python blobs come from raw
`git show` metadata and one read-only `git cat-file --batch` process. Renames are
represented as deletion plus addition. Blob decoding honors Python encoding
cookies. JSONL has repo, commit, path, line, kind and message; sorting inside each
commit is the detector's ordering. No network or API calls were made.

The initial run compared nine saved baseline modules byte for byte with
`f377dd4`. Intermediate scans were exploratory; the tables use final scans.
No third-party source was copied into this repository. Seeded code exists only
in memory; regression examples were written from scratch.

| Repository | HEAD | Non-merge commits | With Python changes |
| --- | --- | ---: | ---: |
| oversight | `f377dd45648e` | 174 | 90 |
| click | `2247b35ea1c4` | 2172 | 1318 |
| attrs | `c8bb9bfaa284` | 1834 | 783 |
| requests | `3c7baaa05ab4` | 4884 | 2824 |
| Total | | 9064 | 5015 |

Findings (`before → after`, not a false-positive count):

| Kind | Oversight | click | attrs | requests | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| assertion_loosened | 22 → 15 | 405 → 382 | 370 → 246 | 460 → 189 | 1257 → 832 |
| expected_value_changed | 4 → 4 | 115 → 113 | 12 → 43 | 32 → 24 | 163 → 184 |
| literal_branch | 53 → 53 | 73 → 70 | 17 → 17 | 19 → 19 | 162 → 159 |
| parse_error | 0 → 0 | 6 → 6 | 0 → 0 | 514 → 514 | 520 → 520 |
| skip_added | 1 → 1 | 40 → 40 | 65 → 65 | 12 → 12 | 118 → 118 |
| stub_left | 10 → 10 | 333 → 326 | 55 → 53 | 238 → 230 | 636 → 619 |
| test_removed | 18 → 18 | 140 → 140 | 118 → 109 | 285 → 284 | 561 → 551 |

Total findings: **3417 → 2983**. Parse-error volume remains visible.

## Precision review and corrections

Findings were grouped by kind and message pattern, then checked against diffs.
Human-written history is not automatic negative ground truth: intentionally
removed checks, changed expectations, skips and placeholders are legitimate review
signals under this specification. Counts are review workload, not a precision score.
Repeated passes retained those signals and corrected these provable syntax cases.
All test names below are in `backend/tests/changes/test_shortcut_corpus_regressions.py`.

| Pattern | Representative history | Regression |
| --- | --- | --- |
| Redundant unicode string prefix treated as a semantic edit | click `b444e635` and Python 3 modernization commits | `test_unicode_prefix_only_edit_is_clean`, `test_unicode_prefix_does_not_introduce_literal_branch` |
| Equality/identity operands reversed without changing the check | attrs `c50d5aaa`, `e458448e` | `test_reversing_comparison_is_clean` |
| Literal expectation on the left misclassified as assertion removal | attrs `58d5c0a7`; seeded functions in all four repos | `test_expected_value_on_left_is_reviewed` |
| Local variable rename with the whole setup preserved | attrs `73ae718e` | `test_local_rename_with_unchanged_setup_is_clean` |
| Deprecated unittest assertion aliases modernized | requests unittest migrations | `test_unittest_alias_modernization_is_clean` |
| Equivalent unittest/bare-assert rewrite | requests `ce7c86cc`, `837ba94d` | `test_unittest_to_pytest_migration_is_clean` |
| Callable exception check rewritten as a context manager | requests `837ba94d` | `test_callable_raises_migrates_to_context_manager` |
| Test rename accompanied by a docstring edit | attrs `73ae718e` and other cosmetic renames | `test_rename_with_docstring_edit_is_clean` |
| Existing nested placeholders re-reported after test move/rename | click `8af7ec14` and later test reorganizations | `test_renaming_test_preserves_existing_helper_placeholder`, `test_moving_test_with_import_edit_preserves_placeholder` |
| Simple format-to-f-string and literal-set modernization | click `b444e635`; attrs `980c8b04`, `08f8319e` | `test_syntax_modernization_preserves_check` |
| Parameter column rename with an identical explicit table | attrs `37bc4b87` | `test_parametrize_argument_rename_preserves_check` |
| Identity checks that tighten truthiness/equality | click exception-check modernization | `test_identity_tightening_is_clean` |
| Two equal stacked parameter tables cross-matched when adding a check (found in the repeat pass) | attrs `6fda0a4e`, `5d6d21aa` | `test_identical_parameter_tables_are_not_cross_matched` |

Companion negative tests keep changed calls, fixture swaps, removed assertions,
changed f-string values, shadowed `set`, and replacement tests visible. Callable
`pytest.raises` deletion was an additional recall miss, fixed with
`test_removed_callable_pytest_raises_is_reported`.

Remaining message groups were reviewed as follows:

| Group | Judgment and example |
| --- | --- |
| Removed/replaced tests or deleted test files | Review signal; click `9caedb92` removes tests, requests `ef5875cc` restructures several cases. Arbitrary splitting/refactoring cannot be proven equivalent from syntax alone. |
| Removed assertions / changed checked expressions | Review signal; Oversight `7861d0a0` changes the checks themselves, attrs `f5683b85` changes an adapter API. |
| Removed/changed exception expectations | Review signal; exception type or match changes remain visible. Equivalent callable/with migrations are canceled. |
| Literal/parametrized expected-value changes | Review signal even when legitimate; attrs `38580632`, Oversight `a6765c87`. |
| Added skip/xfail decorators, calls and parameter markers | Review signal; click `262c275d`, attrs `7373d88f`, requests `4e383642`. |
| Production branch on a changed-set test input | Review signal by design, including common literals such as zero; Oversight `eeab73bb`, attrs `e09b1d64`. |
| Newly introduced pass/ellipsis/NotImplementedError/TODO | Review signal, including deliberate no-op callbacks and error handlers; attrs `5d6d21aa`. Old placeholders in matched tests are preserved. |
| Parse errors | Review signal: mostly historical Python 2 syntax unsupported by Python 3.12; no silent conversion or skipped revisions. |

## Recall and stopping point

`tools/shortcut_seed.py` deterministically selects eight assertion-bearing test
functions per repository from sorted HEAD test paths (32 functions, not a random
statistical sample). It mutates their bodies in memory. Fixed equalities,
exception wrappers and tolerances are introduced as mutation baselines where
needed; the tests are parsed, never executed. Production branch seeds use actual
explicit call literals in those tests. Placeholder mutations use the real body
renamed to a production function. This measures requested syntactic detections,
not runtime test correctness or recall on all possible shortcuts.

| Kind | Before | After |
| --- | ---: | ---: |
| assertion_loosened | 128/160 | 160/160 |
| expected_value_changed | 32/64 | 64/64 |
| literal_branch | 20/20 | 20/20 |
| skip_added | 64/64 | 64/64 |
| stub_left | 128/128 | 128/128 |
| test_removed | 64/64 | 64/64 |

Overall: **436/500 (87.2%) → 500/500 (100.0%)**.

Both misses were systematic: reversed expected values (0/32 → 32/32) and
callable `pytest.raises` removal (0/32 → 32/32). Other seeded mutations were
already detected; they remain detected in the final pass.

The repeat passes fixed the stacked-table regression, redundant unicode-prefix noise and the callable-raises
miss. The final seeded pass exceeds the 95% target. Remaining conservative signals
need broader scope/dataflow or runtime semantics; they are documented rather than
extending the detector into a semantic analyzer. No claim of perfect corpus
precision is made.

## Known limits retained

The detector's module docstring records these limits: fixture-return input flow,
dynamic parameter tables, parameter renames through nested scopes, custom
collection/inheritance and dynamic framework aliases; overlapping test renames
(for example attrs `fba94227`, which also changes the API under test); API/helper
refactors and complex formatted/expected expressions; callable/with raises
checks compare exception and match, not callable arguments or `check=` predicates;
non-test function moves/renames may re-report existing placeholders; unknown
runtime approx values cannot establish widening; historical Python 2 syntax is
a parse finding. Broad refactor signals are kept for review. These limits are
outside the seeded mutation families and require disproportionate semantic work.

| Detector file (`backend/src/oversight/changes/`) | Lines |
| --- | ---: |
| `_shortcut_detector.py` | 126 |
| `_shortcuts_approx.py` | 84 |
| `_shortcuts_asserts.py` | 311 |
| `_shortcuts_common.py` | 249 |
| `_shortcuts_equivalence.py` | 239 |
| `_shortcuts_literals.py` | 135 |
| `_shortcuts_normalize.py` | 102 |
| `_shortcuts_skips.py` | 134 |
| `_shortcuts_stubs.py` | 135 |
| `_shortcuts_tests.py` | 247 |

Corpus harness: 208 lines.
Seed runner: 268 lines.

## Gates

- `ruff check`: passed over the repository.
- `ruff format --check`: passed (136 files).
- `pyright`: passed, zero errors or warnings.
- `lint-imports --no-cache`: passed, 10 contracts kept, zero broken.
- Detector and new corpus/seed tests: **179 passed**.
- Detector plus all tools tests: **335 passed, 11 failed**. Every failure is in
  `tools/tests/test_smoke.py`: the sandbox rejects local socket creation with
  `PermissionError: [Errno 1] Operation not permitted`.
- Detector plus tools excluding that socket-dependent file: **331 passed**.
- `git diff --check`: passed. No git writes, commits, merges, installs, or changes
  to `.pnpm-store/` were performed.

Reproduce a scan with `.venv/bin/python -W ignore tools/shortcut_corpus.py REPO
--revision HEAD --output .eval-output/shortcut-corpus/new-NAME.jsonl`; use a distinct
output filename for each run. Run seeded recall with `.venv/bin/python -W ignore
tools/shortcut_seed.py REPO... --output .eval-output/shortcut-corpus/new-recall.jsonl`.

## Precision round (historical)

Baseline implementation: `26fc6250950a0eea3f079804900d29d95cf7c468`. The sections
above describe the previous hardening round; this section supersedes its policy
for ordinary guards, comment markers, no-ops, and test matching. Contracts and
product integration are untouched. No Git writes in this worktree, network, API credits, installs
or changes to `.pnpm-store/` were made.

### Labeled Oversight evaluation

Oversight history is pinned to `f377dd45648e0e45d85af6afd112aca8e12a3ccc`
(174 non-merge commits, 90 with Python changes), exactly the history covered by
the 101 reviewer-labeled rows. Scanning the implementation commit itself would
change the evaluation population. The unchanged baseline was rerun and reproduced
all 101 findings. Labels were read only from the supplied `oversight-labels.jsonl`.
`tools/shortcut_precision.py` compares `(commit, path, kind)`, ignoring line and
message. All rows sharing a surviving identity count as kept, including NOISE
rows colliding with USEFUL ones. Coalescing therefore does not manufacture a
precision gain.

| Kind | SHORTCUT before → kept | USEFUL before → kept | NOISE before → kept |
| --- | ---: | ---: | ---: |
| assertion_loosened | 1 → 1 | 2 → 2 | 12 → 10 |
| expected_value_changed | 0 → 0 | 4 → 4 | 0 → 0 |
| literal_branch | 0 → 0 | 0 → 0 | 53 → 0 |
| skip_added | 0 → 0 | 0 → 0 | 1 → 1 |
| stub_left | 0 → 0 | 0 → 0 | 10 → 0 |
| test_removed | 0 → 0 | 9 → 8 | 9 → 4 |
| Total | 1 → 1 | 15 → 14 | 85 → 15 |

**70/85 NOISE rows removed (82.4%); 1/1 SHORTCUT and 14/15 USEFUL kept.**
Actual alert count falls **101 → 24**. Of those 24 unique identities, 1 has a
SHORTCUT label, 10 have a USEFUL label, 9 have only NOISE labels, and 4 are new
and unlabelled. On the 20 surviving labeled identities the signal fraction is
11/20 (55%); on kept original label rows it is 15/30 (50%). Neither is a complete
precision estimate for all 24 alerts; the four new identities need labeling.

The one USEFUL removal identity lost is `812ab678`,
`tools/tests/test_check_commits.py`: the multi-ref case is paired with its
replacement. It remains reviewable as `assertion_loosened`, which correctly
counts as lost under the required same-kind metric. New unlabeled identities:

| Commit | Path | Kind |
| --- | --- | --- |
| `23e38811` | `backend/tests/graph/test_layout.py` | assertion_loosened |
| `2b5a9b4e` | `backend/tests/workspace/test_runner.py` | assertion_loosened |
| `812ab678` | `tools/tests/test_check_commits.py` | assertion_loosened |
| `912a6b80` | `backend/tests/agents/test_loop_sink.py` | expected_value_changed |

Every batch was checked with the corpus and the unchanged legacy seed harness:

| Batch | Alerts | SHORTCUT kept | USEFUL kept | NOISE kept | Legacy recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| batch1 | 38 | 1 | 15 | 22 | 480/500 |
| batch2 | 29 | 1 | 13 | 18 | 480/500 |
| batch3 | 25 | 1 | 14 | 17 | 480/500 |
| batch4 | 24 | 1 | 14 | 15 | 480/500 |
| accepted | 24 | 1 | 14 | 15 | 480/500 |
| delivered | 24 | 1 | 14 | 15 | 480/500 |

Batch 2 failed the useful-retention gate and was corrected before acceptance.
Legacy recall in every batch is 100% in the five non-branch families and 0/20
in the branch family; the specification conflict is detailed below.

### Patterns and regression tests

All new examples were written from scratch. No hold-out examples were inspected
or copied. Existing tests contradicting the new brief were updated explicitly;
the pre-existing 546-line detector test file was split without dropping cases.

| Pattern | Regression (under `backend/tests/changes/`) |
| --- | --- |
| Excluded sentinels; guards without expected outputs; per-test input/output pairing | `test_shortcut_precision_shapes.py`: `test_ordinary_sentinel_guard_is_clean`, `test_guard_with_unrelated_result_is_clean`, `test_expected_value_from_another_test_does_not_match` |
| Hardcoded returns and assignments still found | `test_shortcut_precision_shapes.py`: `test_matching_expected_result_is_hardcoding` |
| Except/with/override/visitor no-ops and descriptive comments are clean | `test_shortcut_precision_shapes.py`: `test_intentional_noop_or_comment_is_clean` |
| Whole-function stubs and introduced actionable TODO/NotImplementedError retained | `test_shortcut_precision_shapes.py`: `test_unimplemented_non_test_function_is_reported`, `test_conditional_not_implemented_is_reported`, `test_actionable_todo_comment_is_still_reported` |
| Renames/moves with preserved or additional checks; unmatched removals retained | `test_shortcut_precision_checks.py`: `test_renamed_strengthened_test_is_not_removed`, `test_renaming_with_fewer_checks_stays_removed` |
| Renaming cannot silently hide a weakened or changed check | `test_shortcut_precision_checks.py`: `test_renaming_does_not_hide_loosening`, `test_renamed_test_with_changed_expectation_is_reviewed` |
| Tuple grouping, moved checks, setup edits and local renames | `test_shortcut_precision_checks.py`: `test_tuple_grouping_keeps_checks`, `test_moving_check_inside_a_block_is_clean`, `test_setup_lines_never_count_as_checks`, `test_local_rename_with_added_setup_keeps_checks` |
| Adding raises match strengthens; removing it weakens | `test_shortcut_precision_checks.py`: `test_adding_exception_match_strengthens_the_check` |
| Numeric wording may change while constraints survive; exact-count weakening retained | `test_shortcut_precision_checks.py`: `test_rewording_retains_explicit_numeric_constraints`, `test_exact_count_message_loosened_remains_a_finding`, `test_rewording_does_not_hide_weaker_numeric_language` |
| One alert per path/kind per detector call, with all lines listed | `test_shortcut_precision_checks.py`: `test_findings_are_grouped_by_kind_and_path_with_all_lines` |
| Coalescing cannot game labeled retention; seeds retain explicit shape | `tools/tests/test_shortcut_precision.py`, `tools/tests/test_shortcut_corpus.py` |

### Seeded recall: two definitions kept visible

The original 20 branch seeds return `None`, without any requirement that the
branch output equal a test expectation; several also use newly excluded guard
literals. Detecting all of them conflicts with this precision brief. The default
seed CLI remains the legacy version, and its failed per-kind gate is reported.
`--precision-branches` adds an explicit `operation(input) == 7123` check to the
same sampled test, returns 7123 from the seeded branch, and replaces an excluded
input with a deterministic descriptive string. It keeps the same 20 branch
cases and the other 480 mutations. This is an explicit definition change, not
evidence that legacy per-kind recall passed. Neither version executes corpus code.

| Kind | Baseline (legacy) | Final (legacy) | Baseline (precision shape) | Final (precision shape) |
| --- | ---: | ---: | ---: | ---: |
| assertion_loosened | 160/160 | 160/160 | 160/160 | 160/160 |
| expected_value_changed | 64/64 | 64/64 | 64/64 | 64/64 |
| literal_branch | 20/20 | 0/20 | 20/20 | 20/20 |
| skip_added | 64/64 | 64/64 | 64/64 | 64/64 |
| stub_left | 128/128 | 128/128 | 128/128 | 128/128 |
| test_removed | 64/64 | 64/64 | 64/64 | 64/64 |
| Total | 500/500 | 480/500 (96%) | 500/500 | 500/500 (100%) |

Precision-shape recall passes 95% overall and per kind. The legacy branch-family
gate remains open for the architect to resolve against the revised definition.

### Hold-out counts only

No hold-out findings or diffs were used for tuning. Full histories and HEADs are
identical to the preceding corpus table. Before counts come from the supplied
hardening summaries. These are workload counts, not labeled precision claims.

| Kind | click before → after | attrs before → after | requests before → after |
| --- | ---: | ---: | ---: |
| assertion_loosened | 382 → 125 | 246 → 110 | 189 → 78 |
| expected_value_changed | 113 → 40 | 43 → 31 | 24 → 16 |
| literal_branch | 70 → 1 | 17 → 0 | 19 → 1 |
| parse_error | 6 → 6 | 0 → 0 | 514 → 514 |
| skip_added | 40 → 27 | 65 → 37 | 12 → 11 |
| stub_left | 326 → 119 | 53 → 18 | 230 → 52 |
| test_removed | 140 → 40 | 109 → 38 | 284 → 57 |
| Total | 1077 → 358 | 533 → 234 | 1272 → 729 |

### Gates and remaining scope

- `ruff check .`: passed.
- `ruff format --check .`: passed (142 Python files).
- `pyright`: passed, zero errors or warnings.
- `lint-imports --no-cache`: passed, 10 contracts kept.
- Detector and all tools tests: **384 passed, 11 failed**. All 11 failures are in
  `tools/tests/test_smoke.py`: socket creation is denied by the sandbox
  (`PermissionError: [Errno 1] Operation not permitted`). No tests were skipped
  or changed to hide that environment restriction.
- `git diff --check`: passed. Changes remain uncommitted for coordinator review.

Remaining noise includes helper extraction, broad API/setup refactors, conditional
skip guards, and identities shared by a useful change and redundant old findings.
Test similarity and numeric wording remain bounded heuristics, not a proof of
semantic equivalence; arbitrary helper/dataflow rewrites are not expanded. Empty
methods on external subclasses are treated as intended overrides. Expected values
must be explicit literals or explicit parameter rows. Four new Oversight identities
and the hold-out sample still need independent labels. The legacy branch recall
conflict and socket-dependent smoke tests remain open.

### File sizes and reproduction

| File | Lines |
| --- | ---: |
| `backend/src/oversight/changes/_shortcut_detector.py` | 150 |
| `backend/src/oversight/changes/_shortcuts_approx.py` | 84 |
| `backend/src/oversight/changes/_shortcuts_asserts.py` | 325 |
| `backend/src/oversight/changes/_shortcuts_common.py` | 249 |
| `backend/src/oversight/changes/_shortcuts_equivalence.py` | 239 |
| `backend/src/oversight/changes/_shortcuts_literals.py` | 199 |
| `backend/src/oversight/changes/_shortcuts_normalize.py` | 157 |
| `backend/src/oversight/changes/_shortcuts_skips.py` | 134 |
| `backend/src/oversight/changes/_shortcuts_stubs.py` | 164 |
| `backend/src/oversight/changes/_shortcuts_tests.py` | 330 |
| `backend/src/oversight/changes/_shortcuts_wording.py` | 61 |
| `backend/tests/changes/test_shortcut_corpus_regressions.py` | 220 |
| `backend/tests/changes/test_shortcut_detector.py` | 286 |
| `backend/tests/changes/test_shortcut_detector_fix2.py` | 185 |
| `backend/tests/changes/test_shortcut_detector_fix3.py` | 226 |
| `backend/tests/changes/test_shortcut_detector_round1.py` | 272 |
| `backend/tests/changes/test_shortcut_fix3_brake_commit.py` | 220 |
| `backend/tests/changes/test_shortcut_fix3_move_commit.py` | 322 |
| `backend/tests/changes/test_shortcut_fix3_protocol_commit.py` | 65 |
| `backend/tests/changes/test_shortcut_precision_checks.py` | 127 |
| `backend/tests/changes/test_shortcut_precision_shapes.py` | 80 |
| `tools/shortcut_corpus.py` | 208 |
| `tools/shortcut_seed.py` | 295 |
| `tools/shortcut_precision.py` | 86 |
| `tools/tests/test_shortcut_corpus.py` | 95 |
| `tools/tests/test_shortcut_precision.py` | 37 |
| `tools/shortcut_corpus_report.md` | 360 |

Reproduce a corpus scan with `.venv/bin/python -W ignore tools/shortcut_corpus.py
REPO --revision REV --output .eval-output/shortcut-precision/new-NAME.jsonl`.
Reproduce labeled retention with `.venv/bin/python tools/shortcut_precision.py
LABELS BEFORE AFTER --output .eval-output/shortcut-precision/new-precision.json`.
Reproduce seeds with `.venv/bin/python -W ignore tools/shortcut_seed.py REPO...
--output .eval-output/shortcut-precision/new-recall.jsonl`; add `--precision-branches`
for the revised definition. Use distinct output names to preserve evidence.

## Review round 7

Baseline detector and corpus revision: `8826eb8`. Opaque imported calls now
cancel old helper calls only when that helper has no matched destination.
Removing run()/join() while retaining subprocess.run()/os.path.join() and the
helper definition reports assertion_loosened; the unchanged cases stay no_facts.
Validation inventories are computed once per old helper and once per visible
imported target, shared by matched tests. Bare validation statements cannot
preserve an extracted asserting helper merely by repeating its old validation.

model_validate/model_validate_json count as helper checks when their results
are returned or used in an expression, including an assignment; bare statements
do not. This deliberately updates the round-6 return-value policy. Assignment
counts even when never read: the detector does not verify schema types or follow
dataflow. The existing 683fe05 extraction regression stays closed. raise
AssertionError, pytest.fail and delegation alone remain untracked.

### Pinned corpus by (commit, path)

Both scans cover **321 non-merge commits, 160 with Python changes** through
8826eb8, using baseline and round-7 implementations on exactly that history.
Both contain **60 facts and 11 unknowns**, no parse errors, across **57 identities**.
No (commit, path, kind) or multiplicity changed. Unlisted identities have zero
facts and unknowns. The previous pinned cohorts remain 57 facts + 11 unknowns
through 0df4629 and 58 facts + 11 unknowns through 8c355ed; the two additional
facts in this cohort belong to 8826eb8, rather than a detector regression.

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
| `8826eb8c3d0fd29bdd1ff4a8405ad86923229088` | `backend/tests/changes/test_shortcut_review_round4.py` | 1 | 1 | 0 | 0 |
| `8826eb8c3d0fd29bdd1ff4a8405ad86923229088` | `backend/tests/changes/test_shortcut_review_round5.py` | 1 | 1 | 0 | 0 |
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

### Deadline mutation evidence

Sources are parsed before patching the clock, which advances through integer
reads. Each site below was removed independently and restored in a finally block;
**10/10 named tests failed with exit 1**. Later passes are isolated where needed.
Tests are in backend/tests/changes/test_shortcut_deadlines.py; source paths are
relative to backend/src/oversight/changes. The round-3 end-to-end expiry test also
uses a positive budget and advancing clock instead of timeout=0. The 20,000-call
round-6 test guards the quadratic scan only, not individual deadline sites.

| Removed site | Failing test |
| --- | --- |
| `_shortcuts_equivalence.py:121` | `test_body_key_crosses_deadline_in_each_pass[scope]` |
| `_shortcuts_equivalence.py:131` | `test_body_key_crosses_deadline_in_each_pass[body]` |
| `_shortcuts_normalize.py:210` | `test_normalize_entry_observes_clock_advancing` |
| `_shortcuts_normalize.py:215` | `test_normalize_local_walk_crosses_deadline` |
| `_shortcuts_wording.py:46` | `test_wording_components_cross_deadline` |
| `_shortcuts_common.py:330` | `test_transformer_crosses_deadline_during_visit` |
| `_shortcuts_tests.py:390` | `test_row_candidate_wrappers_cross_deadline_independently[cancel]` |
| `_shortcuts_tests.py:405` | `test_row_candidate_wrappers_cross_deadline_independently[pair]` |
| `_shortcuts_asserts.py:326` | `test_approx_candidates_cross_deadline` |
| `_shortcuts_asserts.py:382` | `test_assertion_pair_loop_crosses_deadline` |

### Retained measurements and reproduction

Historical measurements from rounds 5 and 6 are retained here in place of their
separate reports. They are wall times for fixed synthetic inputs on one machine,
not portable performance thresholds or preemptive timeout guarantees.

| Input | Baseline | Fixed | Status before → after |
| --- | ---: | ---: | --- |
| 150 parametrized methods (round 5) | 2.042 s | 0.166 s | unknown → no_facts |
| 200 parametrized methods (round 5) | 2.009 s | 0.204 s | unknown → no_facts |
| 590 parametrized methods (round 5) | 12.042 s | 0.809 s | unknown → no_facts |
| 20,000 imported calls, 400 files (round 6) | 16.843 s | 2.059 s | unknown → unknown |
| 300 tests, 299 one-assert helpers (round 7) | 1.583 s | 0.555 s | no_facts → no_facts |

Round-5 input: a class with a module-level literal CASES table, changing only a
trailing comment, timeout=2. Round-6 input: the retained regression test, under
the 2 MB cap, timeout=2; the import suffix index is built once per analysis.
Round-7 input: check_i asserts f(i)==3; each test_i calls check_0; only a trailing
comment changes, timeout=120. The baseline helper module is from 8826eb8.
The regression test verifies one validation inventory per helper, including
repeated imported calls, without asserting machine-dependent benchmark times.

Reproduce the corpus command with each detector checkout, keeping the history
revision pinned and distinct outputs: `.venv/bin/python -W ignore
tools/shortcut_corpus.py . --revision 8826eb8 --output
.eval-output/shortcut/fix7-VERSION.jsonl`. Facts exclude unknown and parse_error;
compare counters keyed by (commit, path, kind), not lines or wording.
Required tip gates: `make check` and `.venv/bin/pytest
backend/tests/changes/test_shortcut*.py tools/tests/test_shortcut*.py`.
