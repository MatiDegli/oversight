# Configuration diagnostics: backend hand-back

Packet 1102a, plan revision 2. Work is on `backend/config-diag`, based on
`9b32a31cac9291997e7b3d3c3c1395ccd46a83ec`. No merge, push or provider calls were performed.

## Behavior and boundaries

Configuration classification belongs to `changes/_configuration.py`. It reuses the seven-name
inventory plus exact-case `conftest.py` at any depth. Whole-file object identities include
additions, deletions and non-pytest sections such as `[project].version`. No startup-input
classification or pytest-influence inference was added.

Agent attribution reads `rec.base_commit` under the agent lock and compares it to the captured
`SuiteRun.tested_tree`, never `Tree.base_commit`, cached source maps or working-tree content.
Git output uses the existing capped async runner and context timeout; an overall comparison
deadline also bounds repeated object checks. Malformed, partial, truncated, missing or unreadable
identities and unexpected comparison exceptions become bounded unknown notes. The suite is
published once before comparison; cancellation and evidence-publication failures retain their
existing behavior. Native own-source runs do not receive this first-front diagnostic.

**Single cross-module exception:** the sink Protocol, manager implementation and loop caller
change atomically. The sink returns an optional server note; the loop adds the same text to
content and summary, publishes the step, and then appends the enriched content to model history.
`agents` imports neither classification nor comparison code.

Proposal attribution requires exactly one current-revision proposal execution matching the
change, agent, candidate commit/tree and retained capture. Its prefixed analysis limitation is
frozen in `FactsEvidence.problems` before deferred publication. `SuiteRun.problems` and suite
case completeness receive no advisory text. Existing validators still reject forged receipts.

## Changed files

- `backend/src/oversight/changes/_configuration.py`
- `backend/src/oversight/changes/_facts.py`
- `backend/src/oversight/changes/_manager.py`
- `backend/src/oversight/agents/sink.py`
- `backend/src/oversight/agents/loop.py`
- `backend/tests/agents/agent_helpers.py`
- `backend/tests/changes/test_configuration_attribution.py`
- `backend/tests/changes/test_policy_profiles.py`
- `backend/tests/changes/test_facts_wiring.py`
- `backend/tests/agents/test_loop.py`
- `backend/tests/agents/test_loop_sink.py`
- `DECISIONS.md`: approved boundary, limits and the one cross-module exception.
- `DEVLOG.md`: continuation of the existing November 3 entry.
- `docs/additions.md`: first-front status under facts.
- `docs/configuration-diagnostics-handback.md`: this report.

## Representative notes

Illustrative identities, generated with the production formatter. Every note includes the same
analysis limits; display omission never means comparison incompleteness. Proposal notes use
twenty paths; agent notes use five. Tests exercise both six and twenty-one paths.

**Changed**

```text
configuration attribution: test configuration changed by this change (base 1111111111111111111111111111111111111111): "pyproject.toml"; suite results reflect it; analysis limitation: whole-file matching, pytest relevance unproven; ignored files not compared; configuration outside the repository unknown
```

**Unknown**

```text
configuration attribution: configuration attribution unknown (Git objects unavailable); analysis limitation: whole-file matching, pytest relevance unproven; ignored files not compared; configuration outside the repository unknown
```

**Moved base / inherited configuration**

```text
configuration attribution: no compared configuration differences (base 2222222222222222222222222222222222222222); analysis limitation: whole-file matching, pytest relevance unproven; ignored files not compared; configuration outside the repository unknown
```

**Truncated display (complete comparison of six paths)**

```text
configuration attribution: test configuration changed by this change (base 1111111111111111111111111111111111111111): "tests/00/conftest.py", "tests/01/conftest.py", "tests/02/conftest.py", "tests/03/conftest.py", "tests/04/conftest.py"; 1 paths omitted from display; suite results reflect it; analysis limitation: whole-file matching, pytest relevance unproven; ignored files not compared; configuration outside the repository unknown
```

## Verification

Final targeted run: **230 passed**, two workers, no worker restarts. All five final commands
below returned actual exit code **0**, each run separately without a pipe. Python commands had
provider keys and inherited Git location variables removed, as in the Makefile. Pytest used
`tools/bounded.sh 900` and `tools/capped.sh 10G`; this host has no user systemd memory scope, so
the cap wrapper reported its documented fallback. The worker and timeout safeguards remained.

```sh
uv run --frozen ruff check backend/src/oversight/changes/_configuration.py backend/src/oversight/changes/_facts.py backend/src/oversight/changes/_manager.py backend/src/oversight/agents/sink.py backend/src/oversight/agents/loop.py backend/tests/agents/agent_helpers.py backend/tests/changes/test_configuration_attribution.py backend/tests/changes/test_facts_wiring.py backend/tests/changes/test_policy_profiles.py backend/tests/agents/test_loop.py backend/tests/agents/test_loop_sink.py
```

Exit code: `0`.

```sh
uv run --frozen ruff format --check backend/src/oversight/changes/_configuration.py backend/src/oversight/changes/_facts.py backend/src/oversight/changes/_manager.py backend/src/oversight/agents/sink.py backend/src/oversight/agents/loop.py backend/tests/agents/agent_helpers.py backend/tests/changes/test_configuration_attribution.py backend/tests/changes/test_facts_wiring.py backend/tests/changes/test_policy_profiles.py backend/tests/agents/test_loop.py backend/tests/agents/test_loop_sink.py
```

Exit code: `0`.

```sh
uv run --frozen pyright backend/src/oversight/changes/_configuration.py backend/src/oversight/changes/_facts.py backend/src/oversight/changes/_manager.py backend/src/oversight/agents/sink.py backend/src/oversight/agents/loop.py backend/tests/agents/agent_helpers.py backend/tests/changes/test_configuration_attribution.py backend/tests/changes/test_facts_wiring.py backend/tests/changes/test_policy_profiles.py backend/tests/agents/test_loop.py backend/tests/agents/test_loop_sink.py
```

Exit code: `0`.

```sh
bash tools/bounded.sh 900 bash tools/capped.sh 10G uv run --frozen pytest -n 2 --max-worker-restart=0 backend/tests/changes/test_configuration_attribution.py backend/tests/changes/test_policy_profiles.py backend/tests/changes/test_facts_wiring.py backend/tests/changes/test_suite_publication.py backend/tests/changes/test_revision_context.py backend/tests/agents/test_loop.py backend/tests/agents/test_loop_sink.py
```

Exit code: `0`.

```sh
pnpm -C frontend exec tsc --noEmit
```

Exit code: `0`.

Earlier development verification: the first ruff check with `--fix` returned 1 for long
lines (corrected); the first pytest run returned 1 (215 passed, seven new-test failures);
the next pytest run returned 0 (224 passed), followed by the final 230-pass run after six added
regressions. All format invocations returned 0; both targeted pyright runs returned 0. Frontend
files were not changed, so related Vitest/ESLint did not apply. Full repository gates and
import-linter are left to the coordinating loop as requested.

The tests cover every inventory name, nested files, additions/deletions and version-only edits;
case/startup nonmatches independently of the existing veto; A's merged configuration inherited
by B after a real conflict/Request changes and by a later change; capture-versus-disk drift;
ignored inputs; five/twenty path limits; partial/unreadable output; unknown notes through the real
manager/loop; retained evidence and Stop propagation; frozen facts and candidate association;
and repetition stopping after four identical suites despite differing elapsed times.

## Byte-for-byte veto evidence

Compared exact source spans against the starting commit with `git show` through a timed asyncio
subprocess. All comparisons were equal; SHA-256 values are shared by baseline and final source:

| Source span | SHA-256 |
| --- | --- |
| `integrity_status` (including its predicate) | `74f7effc401cc750751c1ec6771ab66428b9b05b755ce82aea9863b3e6637d29` |
| `_test_integrity` | `470b3b469ce49434f2cf81d132973997e72b077fe2edca0c719f6cb01743d493` |
| `COLLECTION_CONFIG` | `780b0d9efaac3f661d25c15b687dc1cbd4c20d85353466e8d3df415047c07b0b` |

`_policy.py` and `_shortcuts_unknown.py` have no changes. Contracts, generated schemas, frontend,
pytest commands/options/timeouts and README's hackathon provenance are untouched.

## Size exceptions for reviewer judgment

- `agents/loop.py`: 521 lines. Existing lifecycle, cancellation, publication and stop sequencing
  stay together; this change adds enrichment at the existing publication point.
- `changes/_manager.py`: 847 lines. Existing facade remains together; the small sink method owns
  the lock and catches only comparison failures after suite publication.
- `changes/_facts.py`: 513 lines. Existing capture/projection/validation pipeline remains together;
  the added diagnostic is delegated to the new helper. `validate_change_facts` remains an
  unchanged 92-line function so its existing target/evidence invariants remain in one validator.

- `DECISIONS.md`: 4,625 lines, retaining the append-only decision history and its reasons.
- `docs/additions.md`: 1,015 lines, retaining the single existing inventory and item descriptions.

No other changed file exceeds 500 lines and no other function exceeds 80 lines. The new
comparison helper and all new tests stay below those limits.

## Residual limits

Ignored files are not compared; external configuration is unknown. Whole-file matching does not
prove pytest relevance, full-suite coverage or confinement. Displayed path labels are bounded
and disclose shortening independently of path-count omission. The current frontend shows these
advisory analysis limitations under “Incomplete:”; separate prefix-aware rendering is future work.
