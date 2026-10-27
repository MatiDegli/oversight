# Shortcut detector hardening

Baseline: `f377dd45648e0e45d85af6afd112aca8e12a3ccc`. Everything is uncommitted.
The detector remains pure, deterministic, stdlib only, and exported by
`oversight.changes.detect_shortcuts`. No contracts or product integration changed.

## Corpus and method

The complete non-merge ancestry of each local HEAD was scanned, including root
commits and commits without Python changes. Old/new Python blobs come from raw
`git show` metadata and one read-only `git cat-file --batch` process. Renames are
represented as deletion plus addition. Blob decoding honors Python encoding
cookies. JSONL has repo, commit, path, line, kind and message; sorting inside each
commit is the detector's ordering. No network or API calls were made.

Raw evidence is in `/tmp/shortcut-corpus/`: `before-*.jsonl`, `final-*.jsonl`,
their `.summary.json` sidecars, `final-manifest.json`, `group-audit.jsonl`, `recall-before.jsonl`, `recall-final.jsonl`,
`baseline-hashes.json`, and `pytest-gates.txt`. Nine saved baseline modules were
compared byte-for-byte with the corresponding files at `f377dd4`. Intermediate
`round*` and `after-*` files are exploratory; final measurements use `final-*`.
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
  `PermissionError: [Errno 1] Operation not permitted`. Log:
  `/tmp/shortcut-corpus/pytest-gates.txt`.
- Detector plus tools excluding that socket-dependent file: **331 passed**.
- `git diff --check`: passed. No git writes, commits, merges, installs, or changes
  to `.pnpm-store/` were performed.

Reproduce a scan with `.venv/bin/python -W ignore tools/shortcut_corpus.py REPO
--revision HEAD --output /tmp/shortcut-corpus/new-NAME.jsonl`; use a distinct
output filename for each run. Run seeded recall with `.venv/bin/python -W ignore
tools/shortcut_seed.py REPO... --output /tmp/shortcut-corpus/new-recall.jsonl`.
