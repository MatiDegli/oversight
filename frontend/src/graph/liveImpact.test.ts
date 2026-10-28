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
import { liveImpact } from "./impact";

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
