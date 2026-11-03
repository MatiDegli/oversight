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
    "Producer activation",
    "prompt validation",
    "recording",
  ])
    expect(feature).toContain(words);
  expect(readme.split("## How it was built")[1]?.split("\n## ")[0]).not.toContain("Lead Chat");
});
