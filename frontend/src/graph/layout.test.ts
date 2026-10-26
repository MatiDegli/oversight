import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import type { GraphSnapshot, ServerEvent } from "@contracts";
import cytoscape from "cytoscape";
import { describe, expect, it } from "vitest";

import { generateLayout, serializePositions, updateReplay } from "../../tools/layout.ts";
import { applyEvent, createInitialState } from "../state";
import { themes } from "../theme";

import { graphElements } from "./elements";
import { EDGE_CURVE_STYLE, edgeShape, graphStyles } from "./styles";

const root = new URL("../../../", import.meta.url);
const snapshot = JSON.parse(
  readFileSync(new URL("fixtures/graph/demo-repo.json", root), "utf8"),
) as GraphSnapshot;
const fixture = readFileSync(new URL("fixtures/events/handwritten-level1.jsonl", root), "utf8");
const generated = generateLayout(snapshot);
function required<T>(value: T | undefined): T {
  assert.ok(value !== undefined);
  return value;
}

describe("demo preset", () => {
  it("positions every golden node, with finite coordinates and sorted keys", () => {
    expect(Object.keys(generated.positions)).toEqual(snapshot.nodes.map((n) => n.id).sort());
    for (const point of Object.values(generated.positions)) {
      expect(Number.isFinite(point.x)).toBe(true);
      expect(Number.isFinite(point.y)).toBe(true);
    }
    expect(
      JSON.parse(readFileSync(new URL("fixtures/layouts/demo-repo.json", root), "utf8")),
    ).toEqual(generated.positions);
  });

  it("keeps module rectangles disjoint and every descendant inside its parent", () => {
    const blocks = new Map(generated.blocks.map((b) => [b.id, b]));
    const modules = snapshot.nodes
      .filter((n) => n.kind === "module")
      .map((n) => required(blocks.get(n.id)));
    for (const [i, a] of modules.entries())
      for (const b of modules.slice(i + 1)) {
        expect(
          a.x + a.width <= b.x ||
            b.x + b.width <= a.x ||
            a.y + a.height <= b.y ||
            b.y + b.height <= a.y,
          `${a.id} overlaps ${b.id}`,
        ).toBe(true);
      }
    for (const node of snapshot.nodes) {
      if (!node.parent_id) continue;
      const child = required(blocks.get(node.id));
      const parent = required(blocks.get(node.parent_id));
      expect(child.x).toBeGreaterThanOrEqual(parent.x);
      expect(child.y).toBeGreaterThanOrEqual(parent.y);
      expect(child.x + child.width).toBeLessThanOrEqual(parent.x + parent.width);
      expect(child.y + child.height).toBeLessThanOrEqual(parent.y + parent.height);
    }
  });

  it("keeps rendered compound module bounds disjoint at the actual node sizes", () => {
    const state = {
      ...createInitialState(),
      graph: { ...snapshot, positions: generated.positions },
    };
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      elements: graphElements(state, true),
      style: graphStyles(themes.dark),
      layout: { name: "preset" },
    });
    try {
      const modules = cy.nodes(".module").toArray();
      for (const [i, a] of modules.entries())
        for (const b of modules.slice(i + 1)) {
          const x = a.boundingBox();
          const y = b.boundingBox();
          expect(
            x.x2 <= y.x1 || y.x2 <= x.x1 || x.y2 <= y.y1 || y.y2 <= x.y1,
            `${a.id()} overlaps ${b.id()}`,
          ).toBe(true);
        }
    } finally {
      cy.destroy();
    }
  });

  it("projects graph labels above the 15px design floor at 1920x1080 fit", () => {
    const state = {
      ...createInitialState(),
      graph: { ...snapshot, positions: generated.positions },
    };
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      elements: graphElements(state),
      style: graphStyles(themes.dark),
      layout: { name: "preset" },
    });
    try {
      const bounds = cy.elements().boundingBox();
      const zoom = Math.min((1920 - 80) / bounds.w, (1080 - 80) / bounds.h);
      expect(Number.parseFloat(themes.dark["body-size"]) * zoom).toBeGreaterThanOrEqual(15);
    } finally {
      cy.destroy();
    }
  });

  it("produces byte-identical output in separate node processes and for reversed input", async () => {
    const directory = mkdtempSync(join(tmpdir(), "oversight-layout-"));
    try {
      const paths = [join(directory, "first.json"), join(directory, "second.json")];
      for (const output of paths)
        await promisify(execFile)(
          process.execPath,
          [fileURLToPath(new URL("frontend/tools/layout.ts", root)), "--output", output],
          { timeout: 30_000 },
        );
      expect(readFileSync(required(paths[0]), "utf8")).toBe(
        readFileSync(required(paths[1]), "utf8"),
      );
      expect(readFileSync(required(paths[0]), "utf8")).toBe(
        serializePositions(generated.positions),
      );
      expect(
        serializePositions(
          generateLayout({
            ...snapshot,
            nodes: [...snapshot.nodes].reverse(),
            edges: [...snapshot.edges].reverse(),
          }).positions,
        ),
      ).toBe(serializePositions(generated.positions));
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("updates only snapshot positions, preserves other event bytes and is idempotent", () => {
    expect(updateReplay(fixture, generated.positions)).toBe(fixture);
    const events = fixture
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as ServerEvent);
    for (const event of events)
      if (event.type === "graph.snapshot") {
        expect(Object.keys(event.payload.positions).sort()).toEqual(
          event.payload.nodes.map((n) => n.id).sort(),
        );
        for (const node of event.payload.nodes)
          expect(event.payload.positions[node.id]).toEqual(generated.positions[node.id]);
      }
    const otherLines = fixture
      .split(/(?<=\n)/)
      .filter((line) => line.trim() && (JSON.parse(line) as ServerEvent).type !== "graph.snapshot")
      .join("");
    expect(createHash("sha256").update(otherLines).digest("hex")).toBe(
      "c02c05bad09db850592fe7f666e8d17717b1214d535826e8ed71081dd154829d",
    );
    const snapshotLine = JSON.stringify({
      type: "graph.snapshot",
      payload: { ...snapshot, positions: {} },
    });
    const untouched = '  {"type":"agent.step", "payload":{}}  \r\n';
    const log = untouched + snapshotLine + "\r\n" + untouched;
    const updated = updateReplay(log, generated.positions);
    expect(updated.startsWith(untouched)).toBe(true);
    expect(updated.endsWith("\r\n" + untouched)).toBe(true);
    const rewritten = JSON.parse(required(updated.split("\r\n")[1])) as { payload: GraphSnapshot };
    expect(rewritten.payload).toEqual({ ...snapshot, positions: generated.positions });
    expect(() => updateReplay(log, {})).toThrow("Missing preset position:");
    expect(() => updateReplay("invalid", generated.positions)).toThrow();
    expect(() =>
      generateLayout({
        ...snapshot,
        edges: [{ ...required(snapshot.edges[0]), src: "missing", kind: "calls" }],
      }),
    ).toThrow("Invalid graph reference");
  });
});

it("defaults to bezier and applies the dev taxi choice to graph styles", () => {
  expect(EDGE_CURVE_STYLE).toBe("bezier");
  for (const [search, expected] of [
    ["", "bezier"],
    ["?edge-shape=invalid", "bezier"],
    ["?edge-shape=taxi", "taxi"],
    ["?edge-shape=bezier", "bezier"],
  ] as const) {
    expect(edgeShape(search)).toBe(expected);
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      elements: [
        { data: { id: "a" } },
        { data: { id: "b" } },
        { data: { id: "edge", source: "a", target: "b" } },
      ],
      style: graphStyles(themes.dark, edgeShape(search)),
    });
    try {
      expect(cy.edges().first().style("curve-style")).toBe(expected);
    } finally {
      cy.destroy();
    }
  }
});

it("retains the saved positions when the replay adds its delta", () => {
  let state = createInitialState();
  for (const line of fixture.trim().split("\n"))
    state = applyEvent(state, JSON.parse(line) as ServerEvent);
  for (const node of snapshot.nodes.filter((n) => !n.is_test))
    expect(state.graph?.positions[node.id]).toEqual(generated.positions[node.id]);
});
