# Model execution manifest

After `make record RUN=<id>` (or `make record` for the latest finished run), keep the
JSONL and its `.provenance.json` receipt together, then run:

```sh
make manifest LOG=fixtures/events/recorded-demo.jsonl REAL=1
```

This reads contract events and verifies the receipt's hash, run id and event count.
It spends no credits, calls no API and does not start a run. It writes
`<log>.manifest.json` and `<log>.manifest.md`. `OUT=/tmp/demo-manifest` selects a
different prefix; `OUT=/tmp/demo-manifest.json` also works. It does not repeat the
exporter's secret scan or certify provider identity independently of the log.

For the video claim that B used Muse Spark Open, require its exact API id:

```sh
make manifest LOG=fixtures/events/recorded-demo.jsonl REAL=1 EXPECT_B=meta-models/muse-spark-open-1026
```

The equivalent CLI flags are `--out`, `--real` and `--expect-b`. A different
reported model, a false B claim (including B using A's inherited model), or a fake
run claimed as real produces a nonzero exit and one stderr line per failed check.
The artifacts still describe the run and list failed checks. Invalid contracts or
unbound provenance fail before writing. Without `REAL=1`, fake recordings are
accepted and explicitly labeled **scripted fake model**; their calls and costs are
simulated. For example, without changing the fixture:

```sh
make manifest LOG=fixtures/events/scripted-level2.jsonl OUT=/tmp/scripted-manifest
```

The JSON includes each observed agent's requested profile, exact model, provider,
reported model ids and call counts, reasoning mode, prompt version, limits, final
status, last decision and merge result, error counts, event times, usage and cost.
The run's initial merge mode is separate from reasoning mode. Missing fields stay
`null` (`unknown` in Markdown). A missing metric on any call makes that metric's
total unknown; unknown calls are counted both collectively and by metric. No calls
means unknown usage totals. Cached-input share is cached tokens / input tokens,
unknown when either total is unknown or input is zero. Cost sums per-call
`cost_usd`, never the cumulative ledger's `spent_usd`. Times retain UTC from the
events. Slot defaults are offered choices: the agent's `agent.status.model`
selects its actual requested profile. Missing reported model ids cannot verify
`EXPECT_B`, even when its requested model matches.
