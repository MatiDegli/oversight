import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { expect, it } from "vitest";

import { readBoltonsSnapshot } from "./exactFixtures.testSupport";

const root = new URL("../../../", import.meta.url);

it("keeps the Boltons snapshot outside the committed fixture directory", () => {
  expect(existsSync(new URL("fixtures/graph/boltons-d31d821.snapshot.json.base64", root))).toBe(
    false,
  );
});

it("loads only the optional runtime export, applies its preset and reports broken exports", () => {
  const directory = mkdtempSync(join(tmpdir(), "oversight-exact-fixtures-"));
  const localRoot = pathToFileURL(`${directory}/`);
  const graphPath = join(directory, ".runtime/graphs/boltons-d31d821.snapshot.json");
  const layoutPath = join(directory, "fixtures/layouts/boltons-d31d821.json");
  try {
    expect(readBoltonsSnapshot(localRoot)).toBeNull();
    mkdirSync(join(directory, ".runtime/graphs"), { recursive: true });
    mkdirSync(join(directory, "fixtures/layouts"), { recursive: true });
    writeFileSync(graphPath, JSON.stringify({ nodes: [], edges: [], positions: {} }));
    // A present export with a missing preset must fail instead of silently skipping coverage.
    expect(() => readBoltonsSnapshot(localRoot)).toThrow();
    writeFileSync(layoutPath, JSON.stringify({ module: { x: 12, y: 34 } }));
    expect(readBoltonsSnapshot(localRoot)).toEqual({
      nodes: [],
      edges: [],
      positions: { module: { x: 12, y: 34 } },
    });
    writeFileSync(graphPath, "invalid JSON");
    expect(() => readBoltonsSnapshot(localRoot)).toThrow(SyntaxError);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

it("documents the opaque heading and legend plates that mask lines behind text", () => {
  const design = readFileSync(new URL("DESIGN.md", root), "utf8");
  const elevation = design.split("## Elevation & Depth")[1]?.split("### Named Rules")[0];
  expect(elevation).toMatch(/heading and legend use fully opaque `panel` plates/);
  expect(elevation).toMatch(/other graph overlays[\s\S]*90 to 94%/i);
});
