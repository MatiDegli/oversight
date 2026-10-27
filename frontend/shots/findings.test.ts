import assert from "node:assert/strict";
import { test } from "node:test";

import { summarize } from "./findings.ts";

void test("no findings: one line saying so", () => {
  assert.equal(summarize("[]"), "shots: detect found nothing");
});

void test("each finding is a line with its rule, place and detail, after a count by kind", () => {
  const out = summarize(
    JSON.stringify([
      {
        antipattern: "flat-type-hierarchy",
        severity: "advisory",
        advisory: true,
        selector: "body",
        snippet: "Role sizes: body 14px",
      },
      { antipattern: "low-contrast", severity: "error", selector: ".pill", snippet: "2.1:1" },
    ]),
  );
  assert.deepEqual(out.split("\n"), [
    "shots: detect found 2 (1 primary, 1 advisory)",
    "shots:   advisory flat-type-hierarchy at body: Role sizes: body 14px",
    "shots:   error low-contrast at .pill: 2.1:1",
  ]);
});

void test("a finding with missing fields still prints, and a non-list is refused", () => {
  assert.equal(
    summarize('[{"rule":"x"}]').split("\n")[1],
    "shots:   finding unnamed at the page: no detail",
  );
  assert.throws(() => summarize('{"a":1}'), /not a list of findings/);
});
