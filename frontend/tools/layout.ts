// The demo's preset layout, in the Signal sketch's arrangement: modules in columns, calls flowing
// from left to right, with wide gutters between the columns for the edges' lanes. Deterministic: no
// force simulation and no random start, so a take looks the same every time.
// node frontend/tools/layout.ts [--input snapshot.json] [--output positions.json] [--events log.jsonl]
//
// It lays out in two passes, so that a module is a compact block that the zoom views can later
// shrink to a dot at its center: first every module's block is measured from its contents and
// placed among the others, then each module's contents are placed inside its block.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { parseArgs } from "node:util";

import type { GraphNode, GraphSnapshot, Point } from "../../contracts/generated/contracts.ts";
import { FIT_PADDING, nodeGeometry, rowWidthAt } from "../src/graph/geometry.ts";
import { themes } from "../src/theme/index.ts";

import { callColumns } from "./callColumns.ts";

const px = (token: string) => Number.parseFloat(token);
const geometry = nodeGeometry(themes.dark);
/**
 * The graph area of a 1920x1080 take in watch mode (DESIGN.md, Layout: a 52px top bar, a 56px left
 * rail and the 400px agents panel), less the camera's padding: the room the starting view has at
 * 100%. Review mode has less room, but it moves the camera and never the nodes.
 */
export const watchViewport = {
  width:
    1920 -
    px(themes.dark["left-rail-width"]) -
    px(themes.dark["agents-panel-width"]) -
    FIT_PADDING * 2,
  height: 1080 - px(themes.dark["top-bar-height"]) - FIT_PADDING * 2,
};

/** Code-unit order, so the output does not depend on the Node or ICU build. */
const compare = (a: string, b: string): number => (a === b ? 0 : a < b ? -1 : 1);

export interface Block extends Point {
  id: string;
  width: number;
  height: number;
}
interface Tree extends Block {
  children: Tree[];
}

function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Invalid graph reference");
  return value;
}

interface Layout {
  positions: GraphSnapshot["positions"];
  blocks: Block[];
}

/** How many sweeps reorder the modules of each column by the height of the modules they call. */
const SWEEPS = 4;

/**
 * Pass 1: the modules' blocks, in columns. A module's column is how many calls separate it from a
 * module nobody calls. Cycles share a column; terminal modules retain their actual depth, so
 * pricing, the demo hub, occupies the rightmost column. Within a column the order follows the height of connected modules, and
 * each column is centered on one line, so the graph reads left to right.
 */
function arrange(modules: readonly Tree[], calls: readonly (readonly [string, string])[]): Block[] {
  if (!modules.length) return [];
  const ids = modules.map((m) => m.id);
  const known = new Set(ids);
  const links = calls.filter(([a, b]) => a !== b && known.has(a) && known.has(b));
  const callers = new Map(ids.map((id) => [id, new Set<string>()]));
  const callees = new Map(ids.map((id) => [id, new Set<string>()]));
  for (const [a, b] of links) {
    required(callees.get(a)).add(b);
    required(callers.get(b)).add(a);
  }
  const column = callColumns(ids, links);
  const last = Math.max(0, ...column.values());
  const tree = new Map(modules.map((m) => [m.id, m]));
  const columns = Array.from({ length: last + 1 }, (_, k) =>
    ids.filter((id) => column.get(id) === k).sort(compare),
  );
  const heightOf = (column: readonly string[]) =>
    column.reduce((sum, id) => sum + required(tree.get(id)).height, 0) +
    Math.max(0, column.length - 1) * geometry.rowGap;
  const middles = new Map<string, number>();
  const stack = () => {
    for (const members of columns) {
      let y = -heightOf(members) / 2;
      for (const id of members) {
        const height = required(tree.get(id)).height;
        middles.set(id, y + height / 2);
        y += height + geometry.rowGap;
      }
    }
  };
  stack();
  for (let sweep = 0; sweep < SWEEPS; sweep++) {
    for (const members of columns) {
      const key = (id: string) => {
        const near = [...required(callers.get(id)), ...required(callees.get(id))];
        return near.length
          ? near.reduce((sum, n) => sum + required(middles.get(n)), 0) / near.length
          : required(middles.get(id));
      };
      members.sort((a, b) => key(a) - key(b) || compare(a, b));
    }
    stack();
  }
  const blocks: Block[] = [];
  let x = 0;
  for (const members of columns) {
    const width = Math.max(0, ...members.map((id) => required(tree.get(id)).width));
    for (const id of members) {
      const module = required(tree.get(id));
      blocks.push({
        id,
        x,
        y: required(middles.get(id)) - module.height / 2,
        width: module.width,
        height: module.height,
      });
    }
    x += width + geometry.gutter;
  }
  return blocks;
}

function build(snapshot: GraphSnapshot): Layout {
  const nodes = [...snapshot.nodes].sort((a, b) => compare(a.id, b.id));
  const children = new Map<string, GraphNode[]>();
  for (const node of nodes) {
    const siblings = children.get(node.parent_id ?? "") ?? [];
    siblings.push(node);
    children.set(node.parent_id ?? "", siblings);
  }
  // Rows stack in the order of the source, then by id.
  for (const siblings of children.values())
    siblings.sort((a, b) => a.start_line - b.start_line || compare(a.id, b.id));
  // A frame is measured from what it holds: its rows in one column, left-aligned inside its padding.
  // A class is as wide as a row of the frame it sits in, so its own rows are narrower.
  const measure = (node: GraphNode, classes: number): Tree => {
    const frame = node.kind === "module" || node.kind === "class";
    const inside = (children.get(node.id) ?? []).map((child) =>
      measure(child, classes + (node.kind === "class" ? 1 : 0)),
    );
    if (!frame)
      return {
        id: node.id,
        x: 0,
        y: 0,
        width: rowWidthAt(themes.dark, classes),
        height: geometry.rowHeight,
        children: [],
      };
    const pad = geometry.outer[node.kind === "module" ? "module" : "class"];
    if (!inside.length)
      return {
        id: node.id,
        x: 0,
        y: 0,
        width:
          node.kind === "module"
            ? pad.left + geometry.rowWidth + pad.right
            : rowWidthAt(themes.dark, classes),
        height: geometry.headerHeight,
        children: [],
      };
    let y = pad.top;
    const placed = inside.map((child) => {
      const at = { ...child, x: pad.left, y };
      y += child.height + geometry.gap;
      return at;
    });
    return {
      id: node.id,
      x: 0,
      y: 0,
      width: pad.left + Math.max(...inside.map((child) => child.width)) + pad.right,
      height: y - geometry.gap + pad.bottom,
      children: placed,
    };
  };
  const roots = (children.get("") ?? []).map((root) => measure(root, 0));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const moduleOf = (id: string): string => {
    let node = required(byId.get(id));
    while (node.parent_id) node = required(byId.get(node.parent_id));
    return node.id;
  };
  const calls = snapshot.edges
    .filter((edge) => edge.kind === "calls")
    .map((edge) => [moduleOf(edge.src), moduleOf(edge.dst)] as const);
  // Pass 1: whole module blocks, in columns by who calls whom. Tests occupy a separate band below:
  // hiding them must not leave holes in the production graph.
  const isTest = (tree: Tree) => required(byId.get(tree.id)).is_test;
  const production = arrange(
    roots.filter((root) => !isTest(root)),
    calls,
  );
  const top = Math.min(0, ...production.map((b) => b.y));
  const bottom = Math.max(0, ...production.map((b) => b.y + b.height));
  const band = arrange(roots.filter(isTest), calls);
  const bandTop = Math.min(0, ...band.map((b) => b.y));
  const tests = band.map((b) => ({ ...b, y: b.y - bandTop + bottom + geometry.rowGap * 2 }));
  const blocks: Block[] = [];
  const positions: GraphSnapshot["positions"] = {};
  // Pass 2: place each module's measured contents inside its block.
  const place = (tree: Tree, x: number, y: number) => {
    blocks.push({ id: tree.id, x, y, width: tree.width, height: tree.height });
    positions[tree.id] = { x: x + tree.width / 2, y: y + tree.height / 2 };
    for (const child of tree.children) place(child, x + child.x, y + child.y);
  };
  for (const b of [...production, ...tests])
    place(required(roots.find((root) => root.id === b.id)), b.x, b.y - top);
  return {
    positions: Object.fromEntries(Object.entries(positions).sort(([a], [b]) => compare(a, b))),
    blocks,
  };
}

/** Returns conservative outer rectangles too, so tests can check the space each node takes. */
export function generateLayout(snapshot: GraphSnapshot): Layout {
  return build(snapshot);
}

/**
 * A coordinate as the backend's JSON writes the contract's float, which is what Pydantic's
 * `model_dump_json` prints (the event store writes every payload that way), not Python's `repr`:
 * the shortest round-trip digits, always with a point (`1092.0`), in decimal from 1e-5 up to
 * just under 1e16 and in exponent form outside it, with a signed exponent that is not padded
 * (`1e-6`, `1.5e+16`). `repr` writes `1e-06` and `1e-05`. JSON.stringify writes `1092`, and the
 * replay log must round-trip through the event store byte for byte.
 */
export function jsonFloat(value: number): string {
  if (!Number.isFinite(value)) throw new Error(`Coordinate out of range: ${String(value)}`);
  if (value === 0) return Object.is(value, -0) ? "-0.0" : "0.0";
  const [mantissa = "", exponent = ""] = Math.abs(value).toExponential().split("e");
  const power = Number(exponent);
  if (power < -5 || power > 15) {
    const sign = value < 0 ? "-" : "";
    return `${sign}${mantissa}e${power < 0 ? "-" : "+"}${String(Math.abs(power))}`;
  }
  // From 1e-5 to 1e16 JavaScript writes the same shortest digits without an exponent.
  const text = String(value);
  return text.includes(".") ? text : `${text}.0`;
}

const pointJson = ({ x, y }: Point, pretty: boolean) =>
  pretty
    ? `{\n    "x": ${jsonFloat(x)},\n    "y": ${jsonFloat(y)}\n  }`
    : `{"x":${jsonFloat(x)},"y":${jsonFloat(y)}}`;

const positionsJson = (positions: GraphSnapshot["positions"], pretty: boolean): string => {
  const entries = Object.entries(positions).map(
    ([id, point]) =>
      `${pretty ? "  " : ""}${JSON.stringify(id)}:${pretty ? " " : ""}${pointJson(point, pretty)}`,
  );
  if (entries.length === 0) return "{}";
  return pretty ? `{\n${entries.join(",\n")}\n}` : `{${entries.join(",")}}`;
};

export const serializePositions = (positions: GraphSnapshot["positions"]): string =>
  positionsJson(positions, true) + "\n";

const positionsMarker = "\u0000positions";

/** Keep every non-snapshot byte, including CRLFs and the final newline, exactly as supplied. */
export function updateReplay(jsonl: string, positions: GraphSnapshot["positions"]): string {
  return jsonl.replace(/[^\r\n]+/g, (line) => {
    const event = JSON.parse(line) as { type: string; payload: GraphSnapshot };
    if (event.type !== "graph.snapshot") return line;
    const ids = event.payload.nodes.map((n) => n.id);
    for (const id of ids) if (!positions[id]) throw new Error(`Missing preset position: ${id}`);
    // JSON.stringify cannot write 1092.0, so the positions go in as a marker that is replaced.
    const sorted = Object.fromEntries(ids.sort().map((id) => [id, required(positions[id])]));
    event.payload.positions = positionsMarker as unknown as GraphSnapshot["positions"];
    return JSON.stringify(event).replace(JSON.stringify(positionsMarker), () =>
      positionsJson(sorted, false),
    );
  });
}

if (import.meta.main) {
  const root = resolve(import.meta.dirname, "../..");
  const { values } = parseArgs({
    options: {
      input: { type: "string", default: resolve(root, "fixtures/graph/demo-repo.json") },
      output: { type: "string", default: resolve(root, "fixtures/layouts/demo-repo.json") },
      events: { type: "string" },
    },
  });
  const snapshot = JSON.parse(await readFile(values.input, "utf8")) as GraphSnapshot;
  const { positions } = generateLayout(snapshot);
  await mkdir(dirname(values.output), { recursive: true });
  await writeFile(values.output, serializePositions(positions));
  if (values.events)
    await writeFile(values.events, updateReplay(await readFile(values.events, "utf8"), positions));
  console.log(`layout: ${String(Object.keys(positions).length)} positions → ${values.output}`);
}
