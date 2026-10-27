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

import {
  generateLayout,
  jsonFloat,
  serializePositions,
  updateReplay,
  watchViewport,
} from "../../tools/layout.ts";
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

/** The demo's graph as Cytoscape renders it headless, with production nodes only. */
function renderedDemo(positions: GraphSnapshot["positions"]) {
  return cytoscape({
    headless: true,
    styleEnabled: true,
    elements: graphElements({ ...createInitialState(), graph: { ...snapshot, positions } }),
    style: graphStyles(themes.dark),
    layout: { name: "preset" },
  });
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

  it("sizes the preset for the watch viewport that DESIGN.md gives the graph", () => {
    // DESIGN.md, Layout: a 1920x1080 take has a 52px top bar, a 56px rail and a 400px agents panel
    // in watch mode, so the graph gets 1464x1028; the camera fits it with 40px of padding.
    expect(watchViewport).toEqual({ width: 1464 - 80, height: 1028 - 80 });
  });

  it("projects graph labels at design size at the watch viewport's fit", () => {
    const cy = renderedDemo(generated.positions);
    try {
      const bounds = cy.elements().boundingBox();
      const zoom = Math.min((1464 - 80) / bounds.w, (1028 - 80) / bounds.h);
      const label = Number.parseFloat(themes.dark["body-size"]) * zoom;
      // DESIGN.md sets graph labels at 15px; at fit the preset must not shrink them below that.
      expect(label).toBeGreaterThanOrEqual(15);
    } finally {
      cy.destroy();
    }
  });

  it("draws a module or class without children at leaf size, in the container's colors", () => {
    const cy = renderedDemo(generated.positions);
    try {
      const leaf = cy.nodes(".function").first();
      const childless = cy.nodes(".module:childless, .class:childless").toArray();
      expect(childless.map((n) => n.id())).toEqual(
        expect.arrayContaining(["shop", "shop.models:OrderLine", "shop.inventory:OutOfStock"]),
      );
      for (const node of childless) {
        expect([node.id(), node.outerWidth(), node.outerHeight()]).toEqual([
          node.id(),
          leaf.outerWidth(),
          leaf.outerHeight(),
        ]);
        expect(node.style("text-valign")).toBe("center");
        // Still a container to the eye: the module frame's fill, border and label color.
        expect(node.style("border-color")).not.toBe(leaf.style("border-color"));
        expect(node.style("color")).not.toBe(leaf.style("color"));
      }
      for (const node of cy.nodes(".module:parent, .class:parent").toArray())
        expect(node.style("text-valign")).toBe("top");
    } finally {
      cy.destroy();
    }
  });

  it("reserves leaf size for a childless module or class, and every rendered box fits its block", () => {
    const blocks = new Map(generated.blocks.map((b) => [b.id, b]));
    const leaf = required(blocks.get("shop.cart:Cart.add"));
    for (const id of ["shop", "shop.models:OrderLine", "shop.inventory:OutOfStock"])
      expect(required(blocks.get(id)).height, id).toBe(leaf.height);
    const cy = renderedDemo(generated.positions);
    try {
      for (const node of cy.nodes().toArray()) {
        const box = node.boundingBox({ includeLabels: false, includeOverlays: false });
        const block = required(blocks.get(node.id()));
        // The positions are block centers; a rendered body must stay inside its reserved block,
        // give or take the strokes Cytoscape adds to a compound's bounding box (under 2px).
        expect(box.x1, node.id()).toBeGreaterThanOrEqual(block.x - 2);
        expect(box.y1, node.id()).toBeGreaterThanOrEqual(block.y - 2);
        expect(box.x2, node.id()).toBeLessThanOrEqual(block.x + block.width + 2);
        expect(box.y2, node.id()).toBeLessThanOrEqual(block.y + block.height + 2);
      }
    } finally {
      cy.destroy();
    }
  });

  it("keeps siblings apart inside every module and class, below the label band", () => {
    const font = Number.parseFloat(themes.dark["body-size"]);
    const blocks = new Map(generated.blocks.map((b) => [b.id, b]));
    const siblings = new Map<string, GraphSnapshot["nodes"]>();
    for (const node of snapshot.nodes)
      if (node.parent_id)
        siblings.set(node.parent_id, [...(siblings.get(node.parent_id) ?? []), node]);
    expect(siblings.size).toBeGreaterThan(10);
    for (const [parentId, group] of siblings) {
      const parent = required(blocks.get(parentId));
      for (const [i, a] of group.entries()) {
        const x = required(blocks.get(a.id));
        // The label band: the parent's top `font` px, where the label above the frame is drawn
        // (headless Cytoscape has no text metrics, so the label's own box is not measured).
        expect(x.y, a.id).toBeGreaterThanOrEqual(parent.y + font);
        for (const b of group.slice(i + 1)) {
          const y = required(blocks.get(b.id));
          expect(
            x.x + x.width <= y.x ||
              y.x + y.width <= x.x ||
              x.y + x.height <= y.y ||
              y.y + y.height <= x.y,
            `${a.id} overlaps ${b.id}`,
          ).toBe(true);
        }
      }
    }
  });

  it("orders siblings by code unit, not by the ICU collation of the build", () => {
    // "Zeta" sorts before "alpha" by code unit and after it by locale: only the first is portable.
    const parent = required(snapshot.nodes.find((n) => n.kind === "module" && !n.is_test));
    const leaf = required(snapshot.nodes.find((n) => n.kind === "function" && !n.is_test));
    const child = (name: string) => ({
      ...leaf,
      id: `${parent.id}.${name}`,
      name,
      qualname: name,
      parent_id: parent.id,
    });
    const { positions } = generateLayout({
      ...snapshot,
      nodes: [parent, child("alpha"), child("Zeta")],
      edges: [],
    });
    const zeta = required(positions[`${parent.id}.Zeta`]);
    const alpha = required(positions[`${parent.id}.alpha`]);
    expect(zeta.y < alpha.y || (zeta.y === alpha.y && zeta.x < alpha.x)).toBe(true);
  });

  it(
    "produces byte-identical output in separate node processes and for reversed input",
    { timeout: 60_000 },
    async () => {
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
    },
  );

  it("writes a coordinate the way the backend's JSON writes the contract's float", () => {
    // Pydantic dumps Point(x=1092, y=611.5) as {"x":1092.0,"y":611.5}; the event store must
    // reproduce the replay log byte for byte, so integral values keep their ".0".
    expect(jsonFloat(1092)).toBe("1092.0");
    expect(jsonFloat(611.5)).toBe("611.5");
    expect(jsonFloat(1137.3000000000002)).toBe("1137.3000000000002");
    expect(jsonFloat(0)).toBe("0.0");
    expect(jsonFloat(-0)).toBe("-0.0");
    expect(jsonFloat(-5)).toBe("-5.0");
    for (const value of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])
      expect(() => jsonFloat(value)).toThrow("Coordinate out of range");
    const points = { "a.b": { x: 1092, y: 611.5 }, c: { x: 0, y: -3 } };
    expect(serializePositions(points)).toBe(
      '{\n  "a.b": {\n    "x": 1092.0,\n    "y": 611.5\n  },\n  "c": {\n    "x": 0.0,\n    "y": -3.0\n  }\n}\n',
    );
    expect(serializePositions({})).toBe("{}\n");
    const line = JSON.stringify({
      type: "graph.snapshot",
      payload: { ...snapshot, nodes: [], positions: {} },
    });
    expect(updateReplay(line, {})).toBe(line);
    const log = JSON.stringify({
      seq: 1,
      type: "graph.snapshot",
      payload: { nodes: [{ id: "a.b" }, { id: "c" }], positions: {} },
    });
    expect(updateReplay(log, points)).toBe(
      '{"seq":1,"type":"graph.snapshot","payload":{"nodes":[{"id":"a.b"},{"id":"c"}],' +
        '"positions":{"a.b":{"x":1092.0,"y":611.5},"c":{"x":0.0,"y":-3.0}}}}',
    );
  });

  it.each([
    // The text Pydantic's model_dump_json writes for each value, from:
    //   P(x=float(literal)).model_dump_json() with `class P(BaseModel): x: float`
    [1092.0, "1092.0"],
    [611.5, "611.5"],
    [1137.3000000000002, "1137.3000000000002"],
    [0.0, "0.0"],
    [-0.0, "-0.0"],
    [-5.0, "-5.0"],
    [0.1, "0.1"],
    [0.0001, "0.0001"],
    // Decimal down to 1e-5, where Python's repr already writes 1e-05.
    [1e-5, "0.00001"],
    [1.5e-5, "0.000015"],
    [5e-5, "0.00005"],
    [2.5e-5, "0.000025"],
    // Exponent form below it, with the exponent signed and not padded (repr: 1e-06).
    [9.999e-6, "9.999e-6"],
    [1e-6, "1e-6"],
    [1.5e-6, "1.5e-6"],
    [3e-6, "3e-6"],
    [9.999e-7, "9.999e-7"],
    [1e-7, "1e-7"],
    [1.234e-10, "1.234e-10"],
    [5e-324, "5e-324"],
    [-1e-6, "-1e-6"],
    // Decimal up to the largest 16-digit integer, exponent form with a plus from 1e16.
    [1e15, "1000000000000000.0"],
    [1.5e15, "1500000000000000.0"],
    [9999999999999998.0, "9999999999999998.0"],
    [1e16, "1e+16"],
    [1.5e16, "1.5e+16"],
    [-1.5e17, "-1.5e+17"],
    [123456789012345678.0, "1.2345678901234568e+17"],
    [1e21, "1e+21"],
    [1e22, "1e+22"],
    [1e300, "1e+300"],
    [1.7976931348623157e308, "1.7976931348623157e+308"],
  ])("writes %s as %s, the float text Pydantic prints", (value, text) => {
    expect(jsonFloat(value)).toBe(text);
    // JavaScript reads the text back to the same double, so the replay log keeps its value.
    expect(Number(text)).toBe(value);
  });

  it("keeps no integral coordinate in the preset file or the replay snapshot", () => {
    const integral = /"[xy]": ?-?\d+(?![\d.])/;
    const preset = readFileSync(new URL("fixtures/layouts/demo-repo.json", root), "utf8");
    expect(preset).not.toMatch(integral);
    expect(preset).toMatch(/"x": \d+\.0,/);
    const snapshotLines = fixture.split("\n").filter((line) => line.includes('"graph.snapshot"'));
    expect(snapshotLines.length).toBeGreaterThan(0);
    for (const line of snapshotLines) {
      expect(line).not.toMatch(integral);
      expect(line).toMatch(/"x":\d+\.0,/);
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
