import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

it.each(["| Parallel coding agents |", "- **Closed tools.**"])(
  "documents the default harness tool in %s",
  (prefix) => {
    const readme = readFileSync(new URL("../../../../README.md", import.meta.url), "utf8");
    const claim = readme.split("\n").find((line) => line.startsWith(prefix));
    expect(claim).toBeDefined();
    expect(claim).toContain("six fixed tools");
    expect(claim).toContain(
      "plus read-only `find_symbol` while the harness is on (the default), and no shell",
    );
  },
);

it("documents read-history pruning as default-on with an explicit opt-out", () => {
  const readme = readFileSync(new URL("../../../../README.md", import.meta.url), "utf8");
  const next = readme.split("## Next\n")[1]?.split("\n## ")[0];
  const pruning = next?.split("\n- ").find((item) => item.includes("read-history pruning"));
  expect(pruning).toBeDefined();
  expect(pruning).toContain("Measure cost and correctness of the built, default-on");
  expect(pruning).toContain("`OVERSIGHT_HISTORY_PRUNE_TOKENS=0` disables it");
  expect(pruning).toContain("broader history bounds and a visible handoff remain");
});

it("lists lead reference cards as an unreleased capability with its release gates", () => {
  const readme = readFileSync(new URL("../../../../README.md", import.meta.url), "utf8");
  const capabilities = readme.split("## Features")[1]?.split("\n## ")[0];
  const feature = capabilities?.match(
    /^\| Constrained generative UI \|[^\n]*\| Built, not released \|$/m,
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
