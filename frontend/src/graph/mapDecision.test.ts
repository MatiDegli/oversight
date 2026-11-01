import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

it("records the settled map rules in one decision without open disputes", () => {
  const decisions = readFileSync(new URL("../../../DECISIONS.md", import.meta.url), "utf8");
  const mapEntries = decisions
    .split(/^## /m)
    .filter((entry) =>
      /^2026-10-31 — Map (view and folder groups|eligibility and panel ground)\n/.test(entry),
    );
  expect(mapEntries).toHaveLength(1);
  const mapDecision = mapEntries.join("\n");
  expect(mapDecision).not.toContain("CONTESTED");
  expect(mapDecision).toContain("at least 24 module nodes currently shown");
  expect(mapDecision).toContain("26px floor at 0.2");
  expect(mapDecision).toContain("before milestone pulses if ready before the cut line");
});
