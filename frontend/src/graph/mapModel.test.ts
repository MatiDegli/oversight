import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { themes } from "../theme";
import { createInitialState } from "../state";
import { graphElements } from "./elements";
import type { GraphElement } from "./elements";
import { MapLayer } from "./MapLayer";
import { mapModel, panelBox } from "./mapModel";

function elements(): GraphElement[] {
  return ["lib", "misc"].flatMap((group, col) =>
    [0, 1].flatMap((row) => {
      const id = `${group}.${String(row)}`;
      return [
        {
          group: "nodes" as const,
          data: { id, kind: "module" as const, group, title: `${String(row)}.py`, w: 326, h: 70 },
          position: { x: col * 1000, y: row * 300 },
          classes: "module",
        },
        {
          group: "nodes" as const,
          data: { id: `${id}:run`, parent: id, kind: "function" as const, w: 282, h: 46 },
          position: { x: col * 1000, y: row * 300 + 100 },
          classes: "function",
        },
      ];
    }),
  );
}
it("keeps circles equal, counts functions, and shows pending marks per slot only", () => {
  const nodes = elements();
  const pending = nodes.find((n) => n.data.id === "lib.0:run");
  if (!pending) throw new Error("Missing fixture");
  pending.classes += " pending-A pending-B focus-B-edit";
  const read = nodes.find((n) => n.data.id === "misc.0:run");
  if (read) read.classes += " focus-A-read";
  const model = mapModel(nodes, themes.dark);
  expect(model.groups[0]?.modules[0]?.agents).toEqual([
    { slot: "A", text: "Agent A · 1 changed" },
    { slot: "B", text: "Agent B · editing" },
  ]);
  expect(model.groups[1]?.modules.every((m) => !m.agents.length)).toBe(true);
  expect(model.groups.every((g) => g.modules.every((m) => m.functions === 1))).toBe(true);
  const markup = renderToStaticMarkup(
    MapLayer({ frame: { model, level: "map", zoom: 0.3, pan: { x: 0, y: 0 } } }),
  );
  expect(markup.match(/data-map-circle/g)).toHaveLength(4);
  expect(markup).not.toContain("<svg");
  expect(markup).toContain("1 function");
});
it("shares panel top edges despite wrapped headers and keeps the grid independent of folds", () => {
  const model = mapModel(elements(), themes.dark);
  const [left, right] = model.groups;
  if (!left || !right) throw new Error("Missing groups");
  expect(panelBox(left, 0.2, model.groups).y1).toBe(panelBox(right, 0.2, model.groups).y1);
  expect(model).toEqual(mapModel(elements(), themes.dark));
});
it("assigns groups only to drawn modules and keeps tests hidden", () => {
  const nodes = ["lib/a.py", "lib/b.py", "docs/conf.py", "tests/a.py"].map((path) => ({
    id: path,
    path,
    kind: "module" as const,
    name: path,
    qualname: path,
    parent_id: null,
    is_test: path.startsWith("tests/"),
    start_line: 1,
    end_line: 1,
    examples: null,
  }));
  const state = {
    ...createInitialState(),
    graph: {
      nodes,
      edges: [],
      positions: Object.fromEntries(nodes.map((n, i) => [n.id, { x: i * 500, y: 0 }])),
      version: "fixture",
      commit: "fixture",
      parse_errors: [],
    },
  };
  const drawn = graphElements(state);
  expect(drawn.map((e) => [e.data.id, e.data.group])).toEqual([
    ["lib/a.py", "lib"],
    ["lib/b.py", "lib"],
    ["docs/conf.py", "docs"],
  ]);
  const docs = mapModel(drawn, themes.dark).groups.find((g) => g.id === "docs");
  expect(docs?.panel).toBe(false);
  expect(docs?.modules[0]?.name).toBe("docs/conf.py");
  expect(docs?.modules[0]?.functions).toBe(0);
});

it("takes the widest expanded card from the snapshot for the map's cells", () => {
  const nodes = elements().filter((e) => e.data.kind === "module");
  const root = nodes[0];
  if (!root) throw new Error("Missing module");
  root.data.w = 400;
  expect(mapModel(nodes, themes.dark).cellWidth).toBe(520);
});

it("records the contested 26px ground at 20% between stacked panels with wrapped headers", () => {
  const nodes: GraphElement[] = [
    ["library", 30],
    ["helpers", 3],
    ["utilities", 3],
  ].flatMap(([name, count]) =>
    Array.from({ length: Number(count) }, (_, i) => ({
      group: "nodes" as const,
      data: {
        id: `${String(name)}-${String(i)}`,
        kind: "module" as const,
        group: String(name),
        w: 326,
        h: 70,
      },
      classes: "module",
      position: { x: 0, y: i * 220 },
    })),
  );
  const model = mapModel(nodes, themes.dark);
  const first = model.groups.find((g) => g.id === "helpers"),
    next = model.groups.find((g) => g.id === "utilities");
  if (!first || !next) throw new Error("Missing stacked groups");
  first.headerWidth = 140;
  next.headerWidth = 140;
  const gap = (panelBox(next, 0.2, model.groups).y1 - panelBox(first, 0.2, model.groups).y2) * 0.2;
  expect(gap).toBe(26); // 460 × 20% − 60px header − 6px lower padding, below the approved 48px.
});
