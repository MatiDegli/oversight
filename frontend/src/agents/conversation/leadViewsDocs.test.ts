import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

it("lists lead reference cards as an unreleased capability with its release gates", () => {
  const readme = readFileSync(new URL("../../../../README.md", import.meta.url), "utf8");
  const capabilities = readme.split("## What works today")[1]?.split("\n## ")[0];
  const feature = capabilities?.match(
    /- Built, not yet released: Lead Chat[^\n]*(?:\n {2}[^\n]+)*/,
  )?.[0];
  expect(feature).toBeDefined();
  for (const words of [
    "highlight",
    "changed-file",
    "behavior",
    "moment",
    "Synthetic",
    "show_view",
    "advertised and dispatched",
    "paid probe",
    "prompt validation",
    "recording",
  ])
    expect(feature).toContain(words);
  expect(readme.split("## How it was built")[1]?.split("\n## ")[0]).not.toContain("Lead Chat");
});

it.each([
  ["ARCHITECTURE.md", "Producer advertisement"],
  ["PRODUCT.md", "Producer activation"],
  ["README.md", "Producer activation"],
  ["docs/additions.md", "Producer activation"],
])(
  "keeps %s aligned with the shipped view producer and remaining release gates",
  (path, obsolete) => {
    const doc = readFileSync(new URL(`../../../../${path}`, import.meta.url), "utf8");
    expect(doc).not.toContain(obsolete);
    expect(doc).toMatch(/`show_view` is advertised and dispatched/);
    for (const gate of ["paid probe", "prompt validation", "real recording", "bundle replacement"])
      expect(doc).toContain(gate);
    expect(doc).toContain("prompt version");
  },
);

it("keeps the retained browser size rationale accurate and covers the added view proofs", () => {
  const devlog = readFileSync(new URL("../../../../DEVLOG.md", import.meta.url), "utf8");
  for (const name of ["chatLeads.browser.test.ts", "chatLeads.browser.fixture.tsx"]) {
    const source = readFileSync(new URL(`../../app/${name}`, import.meta.url), "utf8");
    const lines = source.trimEnd().split("\n").length;
    expect(devlog).toContain(`${name} (${String(lines)} lines)`);
  }
  const fixture = readFileSync(
    new URL("../../app/leadViews.browser.fixture.tsx", import.meta.url),
    "utf8",
  );
  const scenario = fixture.split("export function viewScenario()")[1]?.split("\nlet player:")[0];
  expect(scenario).toBeDefined();
  const lines = scenario?.trimEnd().split("\n").length;
  expect(devlog).toContain(`viewScenario (${String(lines)} lines)`);
  expect(devlog).toContain("reference-card proofs");
});
