# Configuration attribution review hand-back

Implemented on `frontend/config-attribution`, based on `b378936e`, on November 3, 2026.

The exact, case-sensitive `configuration attribution: ` prefix separates informational notes
from ordinary missing-evidence problems. Each note follows the detector line in its own paragraph:
Next 400, 15px/22px, `text-muted`, preserved whitespace and lossless wrapping, with React text escaping.
Order, duplicates and captured facts remain intact. Attribution alone keeps closed Details reachable
without comparison or other findings, removes the Incomplete paragraph and leaves the standalone
heading as Review signals without claiming completeness. Other problems retain their original
completeness guard, order, wording and semicolon separator, including the empty-list behavior.

Changed files:

- `frontend/src/review/configurationAttribution.ts`: stable prefix and pure partition.
- `frontend/src/review/configurationAttribution.test.ts`: exact matching and retention cases.
- `frontend/src/review/ReviewFacts.tsx`: informational paragraphs and presentation-only separation.
- `frontend/src/review/ReviewFacts.test.tsx`: both modes/completeness values, duplicates, escaped
  suffixes, Details reachability, stale revisions and pre-change output pins.
- `frontend/src/review/reviewFindings.ts`: shared-helper Details eligibility.
- `frontend/src/review/ConfigurationAttribution.browser.fixture.tsx`: labeled synthetic replay
  using `createReplayPlayer`, the shared reducer and production desktop/phone evidence components.
- `frontend/src/review/ConfigurationAttribution.browser.test.tsx`: replay, text, layout and captures.
- `DEVLOG.md`: continuation of the existing November 3 entry.
- This hand-back.

Chat `StepRow` and Oversight agent step history/summary displays were not edited. Neither were
contracts, generated artifacts, backend, recordings, the integrity predicate or `test_integrity`.
No diagnostic prose supplies policy evidence. No model/provider calls, subagents, merges or pushes.
The parallel backend producer has not landed here; real recordings do not acquire invented notes.

## Verification

Commands ran separately, unpiped, using asyncio subprocesses with timeouts. Final results:

| Command | Result | Actual exit code |
| --- | --- | --- |
| `pnpm exec tsc --noEmit` | Strict types pass | 0 |
| Targeted ESLint below | No errors or warnings | 0 |
| Targeted Prettier below | All seven files formatted | 0 |
| Related unit Vitest below | 750 tests, 66 files passed | 0 |
| Browser Vitest below | 10 tests, one file passed | 0 |
| Pydantic event validation below | Two synthetic streams, 21 valid events each | 0 |
| Impeccable detector below | `[]` | 0 |
| `git diff --check` | No whitespace errors | 0 |

From `frontend`:

```sh
pnpm exec tsc --noEmit
pnpm exec eslint --max-warnings 0 src/review/configurationAttribution.ts src/review/configurationAttribution.test.ts src/review/ReviewFacts.tsx src/review/ReviewFacts.test.tsx src/review/reviewFindings.ts src/review/ConfigurationAttribution.browser.fixture.tsx src/review/ConfigurationAttribution.browser.test.tsx
pnpm exec prettier --check src/review/configurationAttribution.ts src/review/configurationAttribution.test.ts src/review/ReviewFacts.tsx src/review/ReviewFacts.test.tsx src/review/reviewFindings.ts src/review/ConfigurationAttribution.browser.fixture.tsx src/review/ConfigurationAttribution.browser.test.tsx
pnpm exec vitest run --project browser src/review/ConfigurationAttribution.browser.test.tsx --maxWorkers 2
```

From the worktree root:

```sh
pnpm -C frontend exec vitest related --run --project unit --maxWorkers 2 src/review/configurationAttribution.ts src/review/configurationAttribution.test.ts src/review/ReviewFacts.tsx src/review/ReviewFacts.test.tsx src/review/reviewFindings.ts src/review/ConfigurationAttribution.browser.fixture.tsx src/review/ConfigurationAttribution.browser.test.tsx
.venv/bin/python -c 'from pathlib import Path; from pydantic import TypeAdapter; from oversight_contracts.events import ServerEvent; adapter = TypeAdapter(ServerEvent); files = list(Path(".shots/configuration-attribution").glob("*-synthetic.jsonl")); assert len(files) == 2; [print(str(path), len([adapter.validate_json(line) for line in path.read_text().splitlines()]), "valid events") for path in files]'
.agents/skills/impeccable/scripts/impeccable detect --json frontend/src/review/ReviewFacts.tsx frontend/src/review/ConfigurationAttribution.browser.fixture.tsx
```

The pre-implementation red runs exited 1 with seven unit failures and eight browser failures;
24 unit and two browser baseline cases passed. Subsequent corrections included lint, a test
expectation about the existing structural-unavailable section, explicit viewport-specific baseline
hashes and the synthetic timestamp's required milliseconds. Final checks above pass. Only targeted
checks ran, following the brief's final rule; full gates remain with the loop. No Python files
changed, so Ruff, Pyright and pytest were not applicable.

## Unchanged baselines

Pre-change pins cover 12 combinations of mode, completeness and ordinary/empty/lookalike problems;
the standalone real-recording output; and both no-comparison body modes without attribution.
The existing whole-output pins remain untouched:
`91a9640747745902514798e79df4d27de268245b8fd3337aaf8e0db4fe024608` and
`bd1f9fed25a8ee2d38543f57909c31a7b65ae21fed56a250a7503495eb656d67`.

The browser loads the complete unmodified `recorded-demo.jsonl` through replay and renders its
first proposal. Its standalone facts HTML remains
`6beacbc7daad667ee199883aeb71d1fe20f0883a8b0fea487745d91d5016c1d6` in both viewports.
The mounted body's pre-change hashes were captured with ReviewFacts and reviewFindings temporarily
restored from `b378936e`, then verified against the implementation:

- 1440px: `7649aa22901cb763606e8492ab24efd635998315f1c5be80c0e2e6cb80d387d7`.
- 390px: `96134db4f4fb1659742eba537279ad4f3fb799289431ab18784bf84be46e7fff`.

## Captures and limits

The browser suite writes eight captures under `.shots/configuration-attribution/`:
`{alone,mixed}-{1440,390}-{dark,day}.png`. They are synthetic evidence, visibly labeled, generated
with Playwright Chromium on Linux using emulated viewport widths; Windows-browser and physical-phone
acceptance were not performed. Automated checks cover exact diagnostic text, escaped markup,
15/22 Next 400, muted text, paragraph bounds, zero horizontal overflow, phone gutters and the 44px
Details disclosure. Seek before/after the facts event and restart remove/restore the same evidence
without duplicates; Details starts closed after remounting. The mixed captures include Incomplete.

Size exceptions for reviewer judgment: `ReviewFacts.test.tsx` has 528 lines because the new matrix
and pre-change pins stay beside the existing review evidence regressions. Its existing describe
callback remains 274 lines. `ReviewFacts` is 125 lines, keeping missing-facts handling and both
presentation modes in the existing component; the change adds the partition and adjacent notes
without reorganizing baseline-sensitive markup. Other changed functions are at most 80 lines and
other changed files are under 500 lines.

Impeccable references read in full: `reference/clarify.md`, `reference/typeset.md`,
`reference/harden.md` (two bounded reads), and `reference/craft-floor.md`, from
`.agents/skills/impeccable/`, plus its `SKILL.md`. DESIGN.md and repository rules governed the work.
