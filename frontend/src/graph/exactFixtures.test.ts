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

const opaquePlates =
  "The graph heading, legend, zoom controls and Modules/Functions/Details control " +
  "(including zoom percentage) use fully opaque `panel` plates to mask lines behind their text.";

it("documents every opaque graph plate that masks lines behind text", () => {
  const design = readFileSync(new URL("DESIGN.md", root), "utf8");
  const elevation = design.split("## Elevation & Depth")[1]?.split("### Named Rules")[0];
  expect(elevation?.replace(/\s+/g, " ")).toContain(opaquePlates);
  expect(elevation?.replace(/\s+/g, " ")).toContain(
    "The legend, zoom controls and node chip popover use opaque `panel` plates with a 12px blur declared behind them.",
  );
  expect(elevation?.replace(/\s+/g, " ")).toContain(
    "Their `frosted` backing declares overlay-panel (84% in dark, 96% in day), but the opaque plate overrides that fill.",
  );
  expect(elevation).toMatch(/Other graph overlays use the 96% overlay-fill with no blur/);
  expect(design.replace(/\s+/g, " ")).toContain(
    "legend, zoom controls and node chip popover use opaque panel plates with blur declared behind them",
  );
  expect(design.replace(/\s+/g, " ")).toContain(
    "The legend, zoom controls and node chip popover declare blur behind opaque plates.",
  );
});

it("documents separate node and module frame opacities at rest", () => {
  const design = readFileSync(new URL("DESIGN.md", root), "utf8").replace(/\s+/g, " ");
  expect(design).toContain("at rest use card at 94%");
  expect(design).toContain("**Rest:** card fill at 94%");
  expect(design).toContain("A module frame: panel fill at module-opacity");
});

it("forbids an Approve key hint while documenting the pointer caption", () => {
  const design = readFileSync(new URL("DESIGN.md", root), "utf8").replace(/\s+/g, " ");
  expect(design).toContain("a hint that advertises an Approve key");
  expect(design).not.toContain("an Approve keyboard hint");
  expect(design).toContain("Approve with a click: it merges into main");
});

it("records the same complete opaque graph plate rule in the exact-call decision", () => {
  const decisions = readFileSync(new URL("DECISIONS.md", root), "utf8");
  const exactCalls = decisions.split(
    "## 2026-10-29 — Exact calls at rest, possible links as quiet context",
  )[1];
  expect(exactCalls?.replace(/\s+/g, " ")).toContain(opaquePlates);
});
