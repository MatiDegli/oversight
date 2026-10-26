// Deterministic rectangular packing. No force simulation or random initial positions.
// node frontend/tools/layout.ts [--input snapshot.json] [--output positions.json] [--events log.jsonl]
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { parseArgs } from "node:util";

import type { GraphNode, GraphSnapshot, Point } from "../../contracts/generated/contracts.ts";
import { themes } from "../src/theme/index.ts";

const px = (token: string) => Number.parseFloat(token);
const font = px(themes.dark["body-size"]);
const gap = px(themes.dark["spacing-lg"]);
const padding = px(themes.dark["spacing-md"]) * 3 + 1;
const leafWidth = font * 10 + 2;
const leafHeight = font * 4 + 2;
/** GraphView fits the camera with this padding on every side (updateGraph.ts). */
const fitPadding = 40;
/**
 * The graph area of a 1920x1080 take in watch mode (DESIGN.md, Layout: a 52px top bar, a 56px left
 * rail and the 400px agents panel), less the fit padding. Review mode has less room, but it moves
 * the camera and never the nodes, so watch mode is the size the preset is judged at.
 */
export const watchViewport = {
  width:
    1920 -
    px(themes.dark["left-rail-width"]) -
    px(themes.dark["agents-panel-width"]) -
    fitPadding * 2,
  height: 1080 - px(themes.dark["top-bar-height"]) - fitPadding * 2,
};

/**
 * Weight of one px of module-to-module call distance against the fit ratio (1 = fills the viewport).
 * 1e-7 only broke ties; 1e-5 pulls called modules together and, at the watch viewport, also lands on
 * the best fit found (labels at 15px or more). 3e-5 and up give up the fit for closer modules.
 */
const callWeight = 1e-5;

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

function shelf(items: readonly Block[], limit: number): Block[] {
  let x = 0;
  let y = 0;
  let rowHeight = 0;
  return items.map((item) => {
    if (x && x + item.width > limit) {
      x = 0;
      y += rowHeight + gap;
      rowHeight = 0;
    }
    const block = { ...item, x, y };
    x += item.width + gap;
    rowHeight = Math.max(rowHeight, item.height);
    return block;
  });
}

function size(blocks: readonly Block[]) {
  return {
    width: Math.max(0, ...blocks.map((b) => b.x + b.width)),
    height: Math.max(0, ...blocks.map((b) => b.y + b.height)),
  };
}

function pack(items: readonly Block[], score: (blocks: Block[]) => number): Block[] {
  if (!items.length) return [];
  const minWidth = Math.max(...items.map((b) => b.width));
  const maxWidth = items.reduce((sum, b) => sum + b.width + gap, 0);
  let best = shelf(items, minWidth);
  let bestScore = score(best);
  // Integer graph units; bounded by the total width, never by convergence or time.
  for (let width = minWidth; width <= maxWidth; width += gap) {
    const candidate = shelf(items, width);
    const value = score(candidate);
    if (value < bestScore) {
      best = candidate;
      bestScore = value;
    }
  }
  return best;
}

/** Every distinct shelf packing of the items, narrowest first, without the dominated ones. */
function shapes(items: readonly Block[]): Block[][] {
  const minWidth = Math.max(...items.map((b) => b.width));
  const maxWidth = items.reduce((sum, b) => sum + b.width + gap, 0);
  const all: Block[][] = [];
  for (let width = minWidth; width <= maxWidth; width += gap) all.push(shelf(items, width));
  return all.filter((a, i) =>
    all.every((b, j) => {
      const [x, y] = [size(a), size(b)];
      const dominated = y.width <= x.width && y.height <= x.height;
      return !dominated || j === i || (y.width === x.width && y.height === x.height && j > i);
    }),
  );
}

interface Layout {
  positions: GraphSnapshot["positions"];
  blocks: Block[];
}

/**
 * One layout. `choice` picks the packing shape of a container by index into its shapes; without
 * one, a container takes the shape nearest twice as wide as tall. `options` counts the shapes of
 * every production container, and `score` is the production graph's fit (lower is better).
 */
function build(
  snapshot: GraphSnapshot,
  choice: ReadonlyMap<string, number>,
  withTests: boolean,
): Layout & { options: Map<string, number>; score: number } {
  const options = new Map<string, number>();
  const nodes = [...snapshot.nodes].sort((a, b) => compare(a.id, b.id));
  const children = new Map<string, GraphNode[]>();
  for (const node of nodes) {
    const siblings = children.get(node.parent_id ?? "") ?? [];
    siblings.push(node);
    children.set(node.parent_id ?? "", siblings);
  }
  const measure = (node: GraphNode): Tree => {
    const descendants = (children.get(node.id) ?? []).map(measure);
    if (!descendants.length)
      return {
        id: node.id,
        x: 0,
        y: 0,
        // A module or class with no children draws at leaf size: its padding and outside label
        // are for parents only (styles.ts).
        width: Math.max(leafWidth, node.name.length * font * 0.6 + 12),
        height: leafHeight,
        children: [],
      };
    descendants.sort((a, b) => b.height - a.height || compare(a.id, b.id));
    const fits = shapes(descendants);
    const area = (blocks: Block[]) => {
      const { width, height } = size(blocks);
      return width * height * (1 + 0.25 * Math.abs(Math.log(width / height / 2)));
    };
    let nearest = 0;
    for (const [i, shape] of fits.entries())
      if (area(shape) < area(required(fits[nearest]))) nearest = i;
    if (!node.is_test) options.set(node.id, fits.length);
    const arranged = required(fits[Math.min(choice.get(node.id) ?? nearest, fits.length - 1)]);
    const bounds = size(arranged);
    return {
      id: node.id,
      x: 0,
      y: 0,
      width: bounds.width + padding * 2,
      height: bounds.height + padding * 2 + font,
      children: arranged.map((b) => ({
        ...required(descendants.find((child) => child.id === b.id)),
        x: b.x + padding,
        y: b.y + padding + font,
      })),
    };
  };
  const roots = (children.get("") ?? []).map(measure);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const moduleOf = (id: string): string => {
    let node = required(byId.get(id));
    while (node.parent_id) node = required(byId.get(node.parent_id));
    return node.id;
  };
  const calls = snapshot.edges
    .filter((edge) => edge.kind === "calls")
    .map((edge) => [moduleOf(edge.src), moduleOf(edge.dst)] as const)
    .filter(([a, b]) => a !== b);
  const degree = (id: string) => calls.filter(([a, b]) => a === id || b === id).length;
  const arrangeModules = (modules: Tree[]): { blocks: Block[]; score: number } => {
    // Highest connectivity first, with stable ties. Call distance breaks equal-fit packings.
    modules.sort((a, b) => degree(b.id) - degree(a.id) || compare(a.id, b.id));
    const score = (blocks: Block[]) => {
      const { width, height } = size(blocks);
      const centers = new Map(
        blocks.map((b) => [b.id, { x: b.x + b.width / 2, y: b.y + b.height / 2 }]),
      );
      const distance = calls.reduce((sum, [a, b]) => {
        const src = centers.get(a);
        const dst = centers.get(b);
        return sum + (src && dst ? Math.abs(src.x - dst.x) + Math.abs(src.y - dst.y) : 0);
      }, 0);
      return (
        Math.max(width / watchViewport.width, height / watchViewport.height) + distance * callWeight
      );
    };
    let best = pack(modules, score);
    // Two bounded pair-swap passes improve adjacency without a stochastic optimizer.
    for (let pass = 0; pass < 2; pass++)
      for (let i = 0; i < modules.length; i++)
        for (let j = i + 1; j < modules.length; j++) {
          const candidate = [...modules];
          [candidate[i], candidate[j]] = [required(candidate[j]), required(candidate[i])];
          const packed = pack(candidate, score);
          if (score(packed) < score(best)) {
            modules = candidate;
            best = packed;
          }
        }
    return { blocks: best, score: score(best) };
  };
  // Pass 1: whole module rectangles, related by module-to-module calls. Tests occupy a separate
  // band: hiding them must not leave holes in the production graph's camera fit.
  const production = arrangeModules(roots.filter((r) => !required(byId.get(r.id)).is_test));
  const testOffset = size(production.blocks).height + gap * 3;
  const tests = withTests
    ? arrangeModules(roots.filter((r) => required(byId.get(r.id)).is_test)).blocks.map((b) => ({
        ...b,
        y: b.y + testOffset,
      }))
    : [];
  const blocks: Block[] = [];
  const positions: GraphSnapshot["positions"] = {};
  // Pass 2: place each module's measured contents; classes remain compact sub-blocks.
  const place = (tree: Tree, x: number, y: number) => {
    blocks.push({ id: tree.id, x, y, width: tree.width, height: tree.height });
    positions[tree.id] = { x: x + tree.width / 2, y: y + tree.height / 2 };
    for (const child of tree.children) place(child, x + child.x, y + child.y);
  };
  for (const b of [...production.blocks, ...tests])
    place(required(roots.find((r) => r.id === b.id)), b.x, b.y);
  return {
    positions: Object.fromEntries(Object.entries(positions).sort(([a], [b]) => compare(a, b))),
    blocks,
    options,
    score: production.score,
  };
}

/**
 * Returns conservative outer rectangles too, so tests can check actual occupied space.
 * Coordinate descent over each production container's packing shape, scored by how well the
 * production graph fits the watch viewport: two bounded passes, no randomness.
 */
export function generateLayout(snapshot: GraphSnapshot): Layout {
  let choice = new Map<string, number>();
  let best = build(snapshot, choice, false);
  for (let pass = 0; pass < 2; pass++)
    for (const [id, count] of [...best.options].sort(([a], [b]) => compare(a, b)))
      for (let index = 0; index < count; index++) {
        const trial = new Map(choice).set(id, index);
        const candidate = build(snapshot, trial, false);
        if (candidate.score < best.score) {
          best = candidate;
          choice = trial;
        }
      }
  const { positions, blocks } = build(snapshot, choice, true);
  return { positions, blocks };
}

export const serializePositions = (positions: GraphSnapshot["positions"]): string =>
  JSON.stringify(positions, null, 2) + "\n";

/** Keep every non-snapshot byte, including CRLFs and the final newline, exactly as supplied. */
export function updateReplay(jsonl: string, positions: GraphSnapshot["positions"]): string {
  return jsonl.replace(/[^\r\n]+/g, (line) => {
    const event = JSON.parse(line) as { type: string; payload: GraphSnapshot };
    if (event.type !== "graph.snapshot") return line;
    const ids = event.payload.nodes.map((n) => n.id);
    for (const id of ids) if (!positions[id]) throw new Error(`Missing preset position: ${id}`);
    event.payload.positions = Object.fromEntries(
      ids.sort().map((id) => [id, required(positions[id])]),
    );
    return JSON.stringify(event);
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
