import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import type { GraphElement } from "./elements";
import type { MapModel } from "./mapModel";
import { headerLines, moduleLayout, panelBox } from "./mapModel";
import {
  chipFractions,
  distanceBadge,
  endPoints,
  mapCalls,
  mapHit,
  mapSegments,
  placeChip,
} from "./mapLines";
import { MapLayer } from "./MapLayer";
import design from "../../../DESIGN.md?raw";
import layerSource from "./MapLayer.tsx?raw";
import linesSource from "./MapLines.tsx?raw";

it("keeps the map layer functions within the 80-line limit", () => {
  for (const [source, kind] of [
    [layerSource, ts.ScriptKind.TSX],
    [linesSource, ts.ScriptKind.TSX],
  ] as const) {
    const file = ts.createSourceFile("map", source, ts.ScriptTarget.Latest, true, kind);
    const functions = file.statements.filter(ts.isFunctionDeclaration);
    expect(functions.length).toBeGreaterThan(0);
    for (const declaration of functions) {
      const start = file.getLineAndCharacterOfPosition(declaration.getStart()).line;
      const end = file.getLineAndCharacterOfPosition(declaration.getEnd()).line;
      expect(end - start + 1, declaration.name?.text).toBeLessThanOrEqual(80);
    }
  }
});

it("preserves the pending name priority and documents built evidence in lines and scope", () => {
  const scale = design.split("### Scale and depth")[1]?.split("### Motion")[0] ?? "";
  const point = (number: number) =>
    scale
      .match(new RegExp(`(?:^|\\n)${String(number)}\\. \\*\\*[^]*?(?=\\n\\d+\\. \\*\\*|$)`))?.[0]
      ?.replace(/\s+/g, " ")
      .trim() ?? "";

  expect(point(6)).toBe(
    "6. **Names.** Constant size on screen. Group names always shown; module names are 13px/600 in " +
      "module-label, centered under the circles, ellipsized at the cell width minus 10px; counts " +
      "are 12px muted. Circle radius is clamp(round(screen cell height × 0.1), 5, 9)px. All colors " +
      "use theme tokens. Target, not built yet: prioritize names an agent touches, the cone reaches " +
      "or the selection holds, then by size; the rest on hover.",
  );
  expect(point(5)).toContain("Hover restores full names");
  expect(point(5)).toContain("85% card backing");
  expect(point(5)).toContain("dim-node");
  expect(point(5)).toContain("names hide with visibility");
  expect(point(8)).toContain("distance badges are 20px");
  expect(point(8)).toContain("panels without members use 0.5 opacity");
});

function fixture() {
  const model: MapModel = {
    cellWidth: 100,
    cellHeight: 220,
    groups: [
      {
        id: "lib",
        label: "lib",
        panel: true,
        x: 0,
        y: 50,
        width: 200,
        height: 200,
        modules: [
          { id: "a", name: "a.py", nameWidth: 30, x: 50, y: 110, functions: 1, agents: [] },
          { id: "b", name: "b.py", nameWidth: 30, x: 150, y: 200, functions: 1, agents: [] },
        ],
      },
      {
        id: "tools",
        label: "tools",
        panel: true,
        x: 400,
        y: 50,
        width: 200,
        height: 200,
        modules: [
          { id: "c", name: "c.py", nameWidth: 30, x: 450, y: 180, functions: 1, agents: [] },
        ],
      },
    ],
  };
  const elements: GraphElement[] = ["a", "b", "c"].flatMap((id) => [
    { group: "nodes", classes: "module", data: { id, kind: "module" } },
    { group: "nodes", classes: "function", data: { id: `${id}:f`, parent: id, kind: "function" } },
  ]);
  const edge = (source: string, target: string, exact = true, rest = false): GraphElement => ({
    group: "edges",
    classes: exact ? "exact" : "possible",
    data: {
      id: `${source}>${target}`,
      kind: "calls",
      source: `${source}:f`,
      target: `${target}:f`,
      restPossible: rest,
    },
  });
  elements.push(edge("a", "c"), edge("a", "c"), edge("b", "a"), edge("c", "b", false, true));
  return { model, elements, edge, frame: { model, zoom: 1, pan: { x: 0, y: 0 } } };
}
it("lifts only the hovered group or the hovered module's own panel when ids collide", () => {
  const { model, frame } = fixture();
  const tools = required(model.groups[1]);
  required(tools.modules[0]).id = "lib";
  for (const kind of ["module", "group"] as const) {
    const markup = renderToStaticMarkup(
      MapLayer({ frame: { ...frame, level: "map", hover: { kind, id: "lib" } } }),
    );
    const panel = (id: string) =>
      markup.match(new RegExp(`<div data-map-group="${id}"[^>]*>`))?.[0];
    expect(panel("lib")?.includes("border-color:var(--edge)")).toBe(kind === "group");
    expect(panel("tools")?.includes("border-color:var(--edge)")).toBe(kind === "module");
  }
});
it("uses only the possible-edge color and width to dim possible call lines", () => {
  const { frame, elements } = fixture();
  const markup = renderToStaticMarkup(
    MapLayer({
      frame: {
        ...frame,
        level: "map",
        calls: mapCalls(elements),
        hover: { kind: "module", id: "b" },
      },
    }),
  );
  const possible = markup.match(/<g data-map-line="possible"[^>]*>[^]*?<\/g>/)?.[0];
  expect(possible).toContain("var(--edge-possible)");
  expect(possible).toContain("var(--possible-edge-width)");
  expect(possible).not.toContain("opacity");
  expect(possible).not.toContain("dim-node");
});
it("hit tests the module block, group header/ground and empty ground after zoom/pan", () => {
  const { model } = fixture(),
    frame = { model, zoom: 0.4, pan: { x: 30, y: 60 } };
  const m = required(required(model.groups[0]).modules[0]),
    box = moduleLayout(m, model, frame.zoom, frame.pan);
  for (const y of [box.y, box.y2 - 1])
    expect(mapHit({ x: box.x, y }, frame)).toEqual({ kind: "module", id: "a" });
  expect(mapHit({ x: 32, y: 60 + 50 * 0.4 - 10 }, frame)).toEqual({ kind: "group", id: "lib" });
  expect(mapHit({ x: 31, y: 60 + 245 * 0.4 }, frame)).toEqual({ kind: "group", id: "lib" });
  expect(mapHit({ x: 330 * 0.4 + 30, y: 100 }, frame)).toBeUndefined();
});
it("has no rest svg and projects incoming/outgoing exact arrows and counts", () => {
  const { elements, frame } = fixture(),
    calls = mapCalls(elements);
  expect(mapSegments({ ...frame, calls })).toEqual([]);
  expect(
    renderToStaticMarkup(MapLayer({ frame: { ...frame, calls, level: "map" } })),
  ).not.toContain("<svg");
  const lines = mapSegments({ ...frame, calls, hover: { kind: "module", id: "a" } });
  expect(lines.map((l) => [l.source, l.target, l.count])).toEqual([
    ["a", "c", 2],
    ["b", "a", 1],
  ]);
  expect(lines[0]?.end.x).toBeGreaterThan(required(lines[0]).start.x);
});
it("group hover includes boundary pairs in both directions and excludes internal pairs", () => {
  const { elements, frame, edge } = fixture();
  elements.push(edge("c", "a"));
  const lines = mapSegments({
    ...frame,
    calls: mapCalls(elements),
    hover: { kind: "group", id: "lib" },
  });
  expect(lines.map((l) => [l.source, l.target])).toEqual([
    ["a", "c"],
    ["c", "b"],
    ["c", "a"],
  ]);
  expect(lines.find((l) => l.possible)).toMatchObject({ chip: undefined });
});
it("permits possible only through restPossible, once per pair, without exact either way", () => {
  const { elements, edge } = fixture();
  elements.push(
    edge("b", "c", false, true),
    edge("c", "a", false, true),
    edge("a", "b", false, true),
  );
  expect(mapCalls(elements).filter((c) => c.possible)).toHaveLength(1);
  const none = elements.filter((e) => e.group === "nodes");
  none.push(edge("a", "c", false));
  expect(mapCalls(none)).toEqual([]);
});
it("skips calls with ends absent from the circles", () => {
  const { frame } = fixture();
  expect(
    mapSegments({
      ...frame,
      calls: [{ source: "a", target: "hidden", count: 1, possible: false }],
      hover: { kind: "module", id: "a" },
    }),
  ).toEqual([]);
});
it("cone arrows reverse callee to caller once, even during hover", () => {
  const { frame, elements } = fixture();
  for (const g of frame.model.groups) for (const m of g.modules) m.inCone = m.id !== "b";
  const calls = mapCalls(elements),
    lines = mapSegments({ ...frame, calls, hover: { kind: "module", id: "a" } });
  expect(lines.filter((l) => l.cone)).toHaveLength(1);
  expect(lines[0]).toMatchObject({ source: "a", target: "c", cone: true, count: 2 });
  expect(required(lines[0]).start.x).toBeGreaterThan(required(lines[0]).end.x);
});
it("badges exclude seeds and agent marks and hiding text retains circle centers", () => {
  const { frame } = fixture(),
    m = required(required(frame.model.groups[0]).modules[0]);
  expect(distanceBadge(m)).toBeUndefined();
  m.distance = 2;
  expect(distanceBadge(m)).toBe(2);
  const before = moduleLayout(m, frame.model, 1, frame.pan);
  m.inCone = false;
  expect(moduleLayout(m, frame.model, 1, frame.pan)).toEqual(before);
  m.agents = [{ slot: "A", text: "Agent A · 1 changed" }];
  expect(distanceBadge(m)).toBeUndefined();
});
it("tries chip fractions in order, avoids text/circles/chips and drops a trapped chip", () => {
  const a = { x: 0, y: 0 },
    b = { x: 1000, y: 0 };
  const occupied: { x1: number; y1: number; x2: number; y2: number }[] = [];
  for (const fraction of chipFractions) {
    const result = placeChip(a, b, 20, [], [], occupied);
    expect(result?.fraction).toBe(fraction);
    occupied.push(required(result).box);
  }
  expect(placeChip(a, b, 20, [], [], occupied)).toBeUndefined();
  expect(placeChip(a, b, 20, [], [{ x: 500, y: 0, radius: 20 }], [])?.fraction).toBe(0.4);
  expect(placeChip(a, b, 20, [{ x1: 480, x2: 520, y1: -10, y2: 10 }], [], [])?.fraction).toBe(0.4);
});
it("insets exact from ring edges by 3/4px, possible by 2px", () => {
  const a = { x: 0, y: 0, radius: 14 },
    b = { x: 100, y: 0, radius: 9 };
  expect(endPoints(a, b)).toEqual({ start: { x: 17, y: 0 }, end: { x: 87, y: 0 } });
  expect(endPoints(a, b, true)).toEqual({ start: { x: 16, y: 0 }, end: { x: 89, y: 0 } });
});
it("wraps measured strip items to 37/60/83px and preserves legacy heights", () => {
  const g = required(fixture().model.groups[0]);
  g.width = 300;
  g.headerItemWidths = [80, 80, 80];
  for (const [zoom, lines, height] of [
    [1, 1, 37],
    [0.65, 2, 60],
    [0.35, 3, 83],
  ]) {
    expect(headerLines(g, required(zoom))).toBe(lines);
    expect((g.y - panelBox(g, required(zoom)).y1) * required(zoom)).toBeCloseTo(required(height));
  }
  delete g.headerItemWidths;
  g.headerWidth = 180;
  expect(g.y - panelBox(g, 1).y1).toBe(37);
  expect((g.y - panelBox(g, 0.4).y1) * 0.4).toBe(60);
});
it("reserves both panel borders when measured header items barely fit", () => {
  const g = required(fixture().model.groups[0]);
  g.width = 300;
  g.headerItemWidths = [140, 137];
  expect(headerLines(g, 1)).toBe(2);
  g.headerItemWidths = [140, 136];
  expect(headerLines(g, 1)).toBe(1);
});

function required<T>(value: T | undefined | null): T {
  if (value === undefined || value === null) throw new Error("Missing fixture value");
  return value;
}
