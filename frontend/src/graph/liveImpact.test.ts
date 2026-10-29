import { readFileSync } from "node:fs";
import type { GraphEdge, GraphNode, ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState } from "../state";
const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
import { liveImpact, possibleCallers } from "./impact";

const node = (id: string, extra: Partial<GraphNode> = {}): GraphNode => ({
  id,
  name: id,
  qualname: id,
  kind: "function",
  path: "demo.py",
  parent_id: null,
  start_line: 1,
  end_line: 2,
  is_test: false,
  examples: null,
  ...extra,
});
const call = (src: string, dst: string, extra: Partial<GraphEdge> = {}): GraphEdge => ({
  id: `${src}-${dst}`,
  src,
  dst,
  kind: "calls",
  confidence: "exact",
  reason: null,
  ...extra,
});
function fixture() {
  const state = structuredClone(
    events.filter((event) => event.seq <= 14).reduce(applyEvent, createInitialState()),
  );
  const change = state.changes["change-1"];
  if (!state.graph || !state.run || !change) throw new Error("Missing replay fixture");
  change.graph = {
    nodes_added: [],
    nodes_modified: ["seed"],
    nodes_removed: [],
    edges_added: [],
    edges_removed: [],
  };
  state.graph = {
    ...state.graph,
    nodes: [
      node("seed"),
      node("a"),
      node("b"),
      node("c"),
      node("possible"),
      node("test", { is_test: true, name: "test_seed" }),
    ],
    edges: [
      call("a", "seed"),
      call("b", "a"),
      call("c", "b"),
      call("seed", "c"),
      call("possible", "seed", { confidence: "possible" }),
      call("test", "seed"),
    ],
  };
  return { state, change };
}

describe("the live impact preview", () => {
  it("walks exact calls through cycles, counts tests, and reports both run limits", () => {
    const { state, change } = fixture();
    if (!state.run) throw new Error("No run");
    state.run.limits.cone_max_depth = 2;
    state.run.limits.cone_max_nodes = 1;
    expect(liveImpact(state, change)).toEqual({
      seeds: ["seed"],
      entries: [{ node_id: "a", distance: 1 }],
      edges_followed: "exact",
      max_depth: 2,
      max_nodes: 1,
      omitted_by_depth: 1,
      omitted_by_limit: 1,
      tests_reaching: 1,
    });
  });

  it("uses added/removed edges and nodes of this change instead of its old proposal", () => {
    const { state, change } = fixture();
    change.graph.edges_removed = ["a-seed"];
    change.graph.nodes_removed = ["test"];
    change.graph.nodes_added = [node("new")];
    change.graph.edges_added = [call("new", "seed"), call("b", "new")];
    expect(liveImpact(state, change)?.entries).toEqual([
      { node_id: "b", distance: 1 },
      { node_id: "c", distance: 2 },
    ]);
  });

  it("follows callers of a class when its constructor changes", () => {
    const { state, change } = fixture();
    if (!state.graph) throw new Error("No graph");
    state.graph.nodes = [
      node("seed", { name: "__init__", kind: "method", parent_id: "Class" }),
      node("Class", { kind: "class" }),
      node("caller"),
    ];
    state.graph.edges = [call("caller", "Class")];
    expect(liveImpact(state, change)?.entries).toEqual([{ node_id: "caller", distance: 1 }]);
  });
});

describe("possible callers outside the exact cone", () => {
  it.each([
    ["shop.inventory:Inventory.reserve", 0, 1],
    ["shop.pricing:compute_price", 9, 2],
    ["shop.cart:Cart.add", 0, 0],
  ] as const)("counts the recorded demo for %s", (seed, size, possible) => {
    const recorded = readFileSync(
      new URL("../../../fixtures/events/recorded-demo.jsonl", import.meta.url),
      "utf8",
    )
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as ServerEvent);
    const state = recorded
      .filter((event) => event.type === "run.started" || event.type === "graph.snapshot")
      .reduce(applyEvent, createInitialState());
    const { change } = fixture();
    change.graph.nodes_modified = [seed];
    const cone = liveImpact(state, change);
    expect(cone?.entries).toHaveLength(size);
    if (!cone) throw new Error("Missing cone");
    expect(possibleCallers(state, change, cone)).toBe(possible);
    const proposed = recorded
      .filter((event) => event.seq <= 63)
      .reduce(applyEvent, createInitialState());
    const actual = Object.values(proposed.changes).find((item) =>
      item.proposal?.impact.seeds.includes(seed),
    );
    if (actual?.proposal) {
      expect(actual.proposal.impact.entries).toHaveLength(size);
      expect(possibleCallers(proposed, actual, actual.proposal.impact)).toBe(possible);
    }
  });

  it("deduplicates one hop, excludes tests, seeds, entries and any exact caller, and applies the overlay", () => {
    const { state, change } = fixture();
    if (!state.graph) throw new Error("Missing graph");
    state.graph.nodes.push(node("next"), node("removed"));
    state.graph.edges.push(
      call("possible", "a", { confidence: "possible" }),
      call("b", "seed", { confidence: "possible" }),
      call("test", "seed", { confidence: "possible" }),
      call("seed", "a", { confidence: "possible" }),
      call("removed", "a", { confidence: "possible" }),
      call("next", "possible", { confidence: "possible" }),
      call("next", "a", { confidence: "possible", kind: "imports" }),
      call("missing", "a", { confidence: "possible" }),
    );
    change.graph.nodes_removed = ["removed"];
    const live = liveImpact(state, change);
    if (!live) throw new Error("Missing cone");
    const cone = { ...live, entries: [{ node_id: "a", distance: 1 }] };
    expect(possibleCallers(state, change, cone)).toBe(1);
    change.graph.edges_added = [call("possible", "a")];
    expect(possibleCallers(state, change, cone)).toBe(0);
    change.graph.edges_removed = ["b-a", "possible-seed"];
    expect(possibleCallers(state, change, cone)).toBe(1);
    expect(possibleCallers({ ...state, graph: null }, change, cone)).toBeNull();
  });
});
