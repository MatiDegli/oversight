# Frozen behavior-change bench

This front supplies data and a blind labeling packet. It runs no detector, arm or model.
The public manifest has 20 independent bench patches and four development patches in a separate
folder. Development patches never enter the sheet or the bench denominators. Each directory
contains `task.txt` and `change.diff`, relative to `demo-repo/`, without changing its template.

## Criteria fixed before execution

Compare the same frozen 20 patches in three arms: tests alone; a Muse Spark reviewer reading the
diff; and behavior diff plus the advisory classification call. Ten construction cases contain a
behavior difference outside their task: none may be let through. The other ten change behavior
only as requested: at most two may prompt an unnecessary question.

Report detection (whether a behavior difference is found) separately from classification
(whether that difference was requested). A planted case passing unchanged through an arm is a
miss, even when it could not be probed. Keep every unprobed case in the original denominators;
report their count and reasons rather than removing them. An empty probe result means only
that these inputs measured no difference. It is not proof of unchanged behavior.

At construction the probe excluded non-literal arguments, side effects, I/O and randomness. The
key's reachability flags record eligibility under those restrictions, not a claim that a bounded
input set detects a case. The product now also builds project objects from constructor and
single-return fixture arguments, and follows method calls on variables assigned from a literal
constructor call, so a case flagged unreachable may be probed. Reports use the
measured unprobed facts, never these flags. Some cases require shop objects; two planted cases
exercise I/O or randomness. Keep development examples separate during tuning and do not tune on
bench labels.

The maintainer must label expected behavior from the task and diff **before anyone sees any
detector output**. Construction intent is in `construction-key.json`, separately from the public
manifest. It is not the maintainer's answer. Keep that file, tests inspecting it and implementation
results away from the labeler. `sheet.py` reads only public artifacts and never reads the key.
Do not include key metadata in any labeling packet or detector prompt.

The key stays tracked publicly before any labeling or detector run: its content and Git history
pre-register construction answers alongside the frozen manifest hash. The maintainer labels from
the shuffled sheet without opening it. Fronts tuning or changing behavior diff use only dev
patches and never read bench patches or the key. The key and its tests change only with a new
manifest version.

All paid benches together share US$20 of the US$150 project budget. First run one pilot task per
arm, then project cumulative spend. If it exceeds the cap, reduce repetitions before removing
arms. A higher-reasoning arm is conditional on the remaining budget. Preserve arms, repetitions
and all historical charges in the shared accounting; never reset a ledger to regain budget.
This data front spends no credits and does not authorize paid execution or add accounting code.

## Check and apply

From the clone root, with the existing locked environment:

```sh
python3 -m tools.eval.behavior.catalog
uv run --offline --frozen pytest tools/eval/tests/test_behavior_patches.py
UV_OFFLINE=1 npm_config_offline=true PNPM_CONFIG_OFFLINE=true make check
```

The catalog checks the baseline tree and both artifacts of every patch. A patch hash is SHA-256
of the UTF-8 encoding of `json.dumps({"task.txt": task, "change.diff": diff}, sort_keys=True,
ensure_ascii=False, separators=(",", ":"))`, with the original file bytes decoded as UTF-8,
including line endings. `manifest.json` itself is pinned in `catalog.py`; there is no automatic
refreeze command. Editing the task, diff or manifest fails closed. The construction key has its
own pinned SHA-256 checked by the construction-balance test; it stays outside the public
fingerprint and is never read to generate a sheet.

Apply exactly one patch to a fresh disposable copy, never to the real template:

```sh
scratch=$(mktemp -d)
cp -R demo-repo/. "$scratch/"
patch_file="$PWD/tools/eval/behavior/patches/bench/<id>/change.diff"
git -C "$scratch" apply --check "$patch_file"
git -C "$scratch" apply "$patch_file"
```

Replace `<id>` with a manifest id. Do not stack patches. The parametrized test makes a new copy
for all 24 patches, applies each with a bounded process helper, and runs all original demo tests
through `tools.eval.grade.suite`: no inherited pytest plugins or provider keys, with its 30-second
deadline and bounded cleanup. Tests and configuration stay unchanged. Evidence lives in pytest
scratch directories. The hash CLI checks integrity; green-test evidence comes from that test.

## Label without evaluation output

```sh
python3 -m tools.eval.behavior.sheet \
  --output /tmp/behavior-sheet.md --labels /tmp/behavior-labels.json \
  --order /tmp/behavior-order.json --seed 1742
```

The order file persists the seed, shuffled bench ids and frozen-manifest fingerprint. Reuse it
and omit `--seed` to reproduce the packet at new output paths. Another seed or altered order is
refused. Existing sheets and labels are never overwritten. Preserve the order and completed
labels outside commits before executing any arm.

For each row in the labels JSON, replace `answer: null` with `"only_asked"`,
`"unasked_difference"` or `"unsure"`. For `unasked_difference`, fill `difference` with the behavior
that was not requested. The Markdown repeats the same three choices and a free-text line for
each patch. Preserve ids and the sheet fingerprint; do not resolve unsure answers using detector
output. The sheet and blank labels contain neither construction fields nor evaluation results.
