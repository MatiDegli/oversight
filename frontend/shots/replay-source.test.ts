import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { scanReplaySource } from "./replay-source.ts";
import { settleLog } from "./scan-log.ts";

const recorded = readFileSync(
  new URL("../../fixtures/events/recorded-demo.jsonl", import.meta.url),
  "utf8",
);
const bundled = {
  text: recorded,
  kind: "real recorded run" as const,
  label: "Recorded run · Oct 27, 2026",
};

void test("the settled recording keeps its provenance label", () => {
  assert.equal(scanReplaySource(bundled, settleLog(recorded)).label, bundled.label);
  assert.equal(scanReplaySource(bundled, settleLog(recorded)).kind, bundled.kind);
});

void test("a different selected log never inherits the bundled recording label or kind", () => {
  const fixture = readFileSync(
    new URL("../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
    "utf8",
  );
  const selected = settleLog(fixture);
  const source = scanReplaySource(bundled, selected);
  assert.equal(source.text, selected);
  assert.equal(source.label, "");
  assert.equal(source.kind, undefined);
  // Even one changed payload means this is no longer the verified recording.
  assert.equal(scanReplaySource(bundled, settleLog(recorded).replace("shop/", "other/")).label, "");
});
