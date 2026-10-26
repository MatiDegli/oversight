// Deterministic rectangular packing. No force simulation or random initial positions.
// node frontend/tools/layout.ts [--input snapshot.json] [--output positions.json] [--events log.jsonl]
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { parseArgs } from "node:util";

import type { GraphNode, GraphSnapshot, Point } from "../../contracts/generated/contracts.ts";
import { themes } from "../src/theme/index.ts";

const font = Number.parseFloat(themes.dark["body-size"]);
const gap = Number.parseFloat(themes.dark["spacing-lg"]);
const padding = Number.parseFloat(themes.dark["spacing-md"]) * 3 + 1;
const leafWidth = font * 10 + 2;
const leafHeight = font * 4 + 2;
const viewport = { width: 1920 - 80, height: 1080 - 80 };

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

/** Returns conservative outer rectangles too, so tests can check actual occupied space. */
export function generateLayout(snapshot: GraphSnapshot): {
  positions: GraphSnapshot["positions"];
  blocks: Block[];
} {
  const nodes = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id, "en"));
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
        width:
          Math.max(leafWidth, node.name.length * font * 0.6 + 12) +
          (["module", "class"].includes(node.kind) ? padding * 2 : 0),
        height: leafHeight + (["module", "class"].includes(node.kind) ? padding * 2 + font : 0),
        children: [],
      };
    descendants.sort((a, b) => b.height - a.height || a.id.localeCompare(b.id, "en"));
    const arranged = pack(descendants, (blocks) => {
      const { width, height } = size(blocks);
      return width * height * (1 + 0.25 * Math.abs(Math.log(width / height / 2)));
    });
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
  const arrangeModules = (modules: Tree[]): Block[] => {
    // Highest connectivity first, with stable ties. Call distance breaks equal-fit packings.
    modules.sort((a, b) => degree(b.id) - degree(a.id) || a.id.localeCompare(b.id, "en"));
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
      return Math.max(width / viewport.width, height / viewport.height) + distance * 0.0000001;
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
    return best;
  };
  // Pass 1: whole module rectangles, related by module-to-module calls. Tests occupy a separate
  // band: hiding them must not leave holes in the production graph's camera fit.
  const production = arrangeModules(roots.filter((r) => !required(byId.get(r.id)).is_test));
  const testOffset = size(production).height + gap * 3;
  const tests = arrangeModules(roots.filter((r) => required(byId.get(r.id)).is_test)).map((b) => ({
    ...b,
    y: b.y + testOffset,
  }));
  const blocks: Block[] = [];
  const positions: GraphSnapshot["positions"] = {};
  // Pass 2: place each module's measured contents; classes remain compact sub-blocks.
  const place = (tree: Tree, x: number, y: number) => {
    blocks.push({ id: tree.id, x, y, width: tree.width, height: tree.height });
    positions[tree.id] = { x: x + tree.width / 2, y: y + tree.height / 2 };
    for (const child of tree.children) place(child, x + child.x, y + child.y);
  };
  for (const b of [...production, ...tests])
    place(required(roots.find((r) => r.id === b.id)), b.x, b.y);
  return {
    positions: Object.fromEntries(
      Object.entries(positions).sort(([a], [b]) => (a === b ? 0 : a < b ? -1 : 1)),
    ),
    blocks,
  };
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
