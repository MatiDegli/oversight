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
import { FIT_PADDING, nodeGeometry } from "./geometry";
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
    expect(watchViewport).toEqual({
      width: 1464 - FIT_PADDING * 2,
      height: 1028 - FIT_PADDING * 2,
    });
  });

  it("draws graph labels at the sketch's size at 100%", () => {
    // At 100%, a name is the sketch's 18px.
    expect(Number.parseFloat(themes.dark["node-name-size"])).toBe(18);
    const cy = renderedDemo(generated.positions);
    try {
      expect(cy.nodes(".function").first().style("font-size")).toBe("18px");
    } finally {
      cy.destroy();
    }
  });

  it("leaves a gutter of at least 60px between modules that sit side by side", () => {
    const modules = snapshot.nodes
      .filter((n) => n.kind === "module")
      .map((n) => required(generated.blocks.find((b) => b.id === n.id)));
    let neighbours = 0;
    for (const a of modules)
      for (const b of modules) {
        // Level in y, so that a call between them runs through the space between them.
        const level = a.y < b.y + b.height && b.y < a.y + a.height;
        if (a === b || !level || b.x < a.x + a.width) continue;
        neighbours++;
        expect(b.x - (a.x + a.width), `${a.id} to ${b.id}`).toBeGreaterThanOrEqual(60);
      }
    expect(neighbours).toBeGreaterThan(0);
    // Stacked modules keep the row gap the stylesheet's frames need between them.
    for (const a of modules)
      for (const b of modules) {
        const overlapsX = a.x < b.x + b.width && b.x < a.x + a.width;
        if (a !== b && overlapsX && a.y < b.y)
          expect(b.y - (a.y + a.height), `${a.id} over ${b.id}`).toBeGreaterThanOrEqual(60);
      }
  });

  it("places the modules in two passes: calls move whole blocks, never what is inside one", () => {
    // Pass 1 puts the blocks by who calls whom; pass 2 places each module's contents inside its
    // own block. So with no calls at all the blocks move, and every node keeps its offset from
    // its module's block.
    const bare = generateLayout({ ...snapshot, edges: [] });
    const origin = (layout: typeof generated, id: string) => {
      let node = required(snapshot.nodes.find((n) => n.id === id));
      while (node.parent_id) node = required(snapshot.nodes.find((n) => n.id === node.parent_id));
      return required(layout.blocks.find((block) => block.id === node.id));
    };
    let moved = 0;
    for (const block of generated.blocks) {
      const here = origin(generated, block.id);
      const there = origin(bare, block.id);
      const alone = required(bare.blocks.find((b) => b.id === block.id));
      expect([block.x - here.x, block.y - here.y], block.id).toEqual([
        alone.x - there.x,
        alone.y - there.y,
      ]);
      if (here.x !== there.x || here.y !== there.y) moved++;
    }
    expect(moved).toBeGreaterThan(0);
  });

  it("flows calls mostly one way, from the left column to the right", () => {
    const blocks = new Map(generated.blocks.map((b) => [b.id, b]));
    const moduleOf = (id: string): string => {
      let node = snapshot.nodes.find((n) => n.id === id);
      while (node?.parent_id) node = snapshot.nodes.find((n) => n.id === node?.parent_id);
      return required(node).id;
    };
    const between = new Set<string>();
    for (const edge of snapshot.edges) {
      if (edge.kind !== "calls") continue;
      const [a, b] = [moduleOf(edge.src), moduleOf(edge.dst)];
      if (a !== b) between.add(`${a}>${b}`);
    }
    let right = 0;
    for (const pair of between) {
      const [a, b] = pair.split(">");
      if (required(blocks.get(required(a))).x < required(blocks.get(required(b))).x) right++;
    }
    expect(between.size).toBeGreaterThan(4);
    expect(right / between.size).toBeGreaterThanOrEqual(0.7);
  });

  it("draws a module or class without children as one header row, in the container's colors", () => {
    const cy = renderedDemo(generated.positions);
    const g = nodeGeometry(themes.dark);
    try {
      const leaf = cy.nodes(".function").first();
      const childless = cy.nodes(".module:childless, .class:childless").toArray();
      expect(childless.map((n) => n.id())).toEqual(
        expect.arrayContaining(["shop", "shop.models:OrderLine", "shop.inventory:OutOfStock"]),
      );
      for (const node of childless) {
        // A header's height; as wide as the module's rows plus its paddings, or as a module's row
        // for a class, which sits in the module's column.
        const width = node.hasClass("module")
          ? g.outer.module.left + g.rowWidth + g.outer.module.right
          : g.rowWidth;
        expect([node.id(), node.outerWidth(), node.outerHeight()]).toEqual([
          node.id(),
          width,
          g.headerHeight,
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

  it("reserves a header's height for a childless module or class, and every rendered box fits its block", () => {
    const blocks = new Map(generated.blocks.map((b) => [b.id, b]));
    for (const id of ["shop", "shop.models:OrderLine", "shop.inventory:OutOfStock"])
      expect(required(blocks.get(id)).height, id).toBe(nodeGeometry(themes.dark).headerHeight);
    const cy = renderedDemo(generated.positions);
    try {
      for (const node of cy.nodes().toArray()) {
        const box = node.boundingBox({ includeLabels: false, includeOverlays: false });
        const block = required(blocks.get(node.id()));
        // The positions are block centers; a rendered body must stay inside its reserved block,
        // give or take the strokes Cytoscape adds to a compound's bounding box: about a pixel
        // for each frame around a node, so up to 4px for a class inside a module.
        expect(box.x1, node.id()).toBeGreaterThanOrEqual(block.x - 4);
        expect(box.y1, node.id()).toBeGreaterThanOrEqual(block.y - 4);
        expect(box.x2, node.id()).toBeLessThanOrEqual(block.x + block.width + 4);
        expect(box.y2, node.id()).toBeLessThanOrEqual(block.y + block.height + 4);
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
    [1.2345678901234568e17, "1.2345678901234568e+17"],
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

it("defaults to routed taxi edges and applies the dev bezier choice to graph styles", () => {
  expect(EDGE_CURVE_STYLE).toBe("taxi");
  for (const [search, expected] of [
    ["", "round-segments"],
    ["?edge-shape=invalid", "round-segments"],
    ["?edge-shape=taxi", "round-segments"],
    ["?edge-shape=bezier", "bezier"],
  ] as const) {
    expect(edgeShape(search)).toBe(search.endsWith("bezier") ? "bezier" : "taxi");
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      elements: [
        { data: { id: "a" } },
        { data: { id: "b" } },
        {
          data: {
            id: "edge",
            source: "a",
            target: "b",
            route: { s: [10, 0], t: [-10, 0], w: [0.5], d: [0] },
          },
        },
        { data: { id: "plain", source: "b", target: "a" } },
      ],
      style: graphStyles(themes.dark, edgeShape(search)),
    });
    try {
      expect(cy.getElementById("edge").style("curve-style")).toBe(expected);
      // An edge nothing has routed yet is a curve, whatever the choice.
      expect(cy.getElementById("plain").style("curve-style")).toBe("bezier");
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
