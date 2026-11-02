import { mark } from "./marks.testSupport";
import { combineMarks } from "./agentMarks";
import { expect, it } from "vitest";
import type { GraphSnapshot, ImpactCone } from "@contracts";
import { createChange, createInitialState } from "../state";
import { themes } from "../theme";
import type { GraphElement } from "./elements";
import { mapModel } from "./mapModel";
import { decisionsLabel, displayName, mapEvidence, testedLabel } from "./mapEvidence";

function fixture() {
  const state = createInitialState();
  const elements: GraphElement[] = ["lib.a", "lib.b", "misc.c"].flatMap((id, i) => [
    {
      group: "nodes",
      classes: "module",
      data: { id, kind: "module", title: `${id}.py`, group: id.split(".")[0] },
      position: { x: i * 500, y: 0 },
    },
    {
      group: "nodes",
      classes: i ? "function focus-B-read" : "function pending-F focus-C-edit pending-A",
      data: {
        id: `${id}:f`,
        kind: "function",
        parent: id,
        marks: combineMarks(i ? [mark("B", "read")] : [mark("F"), mark("C", "editing"), mark("A")]),
      },
      position: { x: i * 500, y: 100 },
    },
  ]);
  const nodes = elements.map((e) => ({
    id: e.data.id,
    parent_id: e.data.parent ?? null,
    is_test: false,
  }));
  state.graph = {
    nodes: [...nodes, { id: "tests:f", parent_id: null, is_test: true }],
    edges: [],
  } as unknown as GraphSnapshot;
  return { state, elements, model: mapModel(elements, themes.dark) };
}
it("collects pending, read and edit rings once each in slot order", () => {
  const { state, elements, model } = fixture();
  mapEvidence(model, elements, state);
  expect(model.groups[0]?.rings?.map((r) => r.letter)).toEqual(["A", "B", "C", "F"]);
  expect(model.groups[1]?.rings?.map((r) => r.letter)).toEqual(["B"]);
});
it("counts ready and conflict changes once per group, including added and removed nodes", () => {
  const { state, elements, model } = fixture();
  for (const [i, status] of (
    ["ready_for_review", "conflict", "approved", "rejected", "editing", "revising"] as const
  ).entries()) {
    const c = createChange(String(i), "a");
    c.status = status;
    c.graph.nodes_modified = ["lib.a:f", "lib.b:f"];
    if (i === 1) c.graph.nodes_removed.push("misc.c:f");
    state.changes[c.change_id] = c;
  }
  const added = createChange("added", "a");
  added.status = "ready_for_review";
  added.graph.nodes_added = [{ id: "lib.b:f" } as GraphSnapshot["nodes"][number]];
  state.changes.added = added;
  mapEvidence(model, elements, state);
  expect(model.groups.map((g) => g.decisions)).toEqual([3, 1]);
  expect(decisionsLabel(1)).toBe("1 decision");
  expect(decisionsLabel(3)).toBe("3 decisions");
  expect(decisionsLabel(0)).toBeUndefined();
});
it("counts only exact test calls into the group, with singular/plural and hidden zero/tests", () => {
  const { state, elements, model } = fixture();
  if (!state.graph) throw new Error("Missing graph");
  state.graph.edges = [
    { id: "one", src: "tests:f", dst: "lib.a:f", kind: "calls", confidence: "exact", reason: null },
    { id: "two", src: "tests:f", dst: "lib.b:f", kind: "calls", confidence: "exact", reason: null },
    {
      id: "possible",
      src: "tests:f",
      dst: "lib.a:f",
      kind: "calls",
      confidence: "possible",
      reason: null,
    },
    {
      id: "product",
      src: "lib.b:f",
      dst: "misc.c:f",
      kind: "calls",
      confidence: "exact",
      reason: null,
    },
  ];
  mapEvidence(model, elements, state);
  expect(model.groups.map((g) => g.tested)).toEqual([2, 0]);
  expect(testedLabel(1)).toBe("tested by 1 call");
  expect(testedLabel(2)).toBe("tested by 2 calls");
  expect(testedLabel(0)).toBeUndefined();
  expect(testedLabel(2, true)).toBeUndefined();
});
it("drops only the leading tests prefix and retains the full name on the module", () => {
  expect(displayName("test_test_thing.py", "tests")).toEqual({
    name: "test_thing.py",
    fullName: "test_test_thing.py",
  });
  expect(displayName("test_thing.py", "lib")).toEqual({ name: "test_thing.py" });
  const { elements } = fixture();
  for (const e of elements)
    if (e.data.kind === "module") {
      e.data.group = "tests";
      e.data.title = "test_thing.py";
    }
  const m = mapModel(elements, themes.dark).groups[0]?.modules[0];
  expect(m).toMatchObject({ name: "thing.py", fullName: "test_thing.py" });
});
it("counts entries per group, projects seeds and keeps the smallest module distance", () => {
  const { state, elements, model } = fixture();
  const cone: ImpactCone = {
    seeds: ["lib.a:f"],
    entries: [
      { node_id: "lib.a:f", distance: 2 },
      { node_id: "lib.b:f", distance: 3 },
      { node_id: "lib.b", distance: 1 },
    ],
    edges_followed: "exact",
    max_depth: 3,
    max_nodes: 40,
    omitted_by_depth: 2,
    omitted_by_limit: 1,
    tests_reaching: 0,
  };
  mapEvidence(model, elements, state, cone);
  expect(model.groups[0]).toMatchObject({ coneCount: 3, coneTotal: 3 });
  expect(model.groups[0]?.modules).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ id: "lib.a", inCone: true, distance: undefined }),
      expect.objectContaining({ id: "lib.b", inCone: true, distance: 1 }),
    ]),
  );
  expect(model.groups[1]?.modules[0]?.inCone).toBe(false);
  mapEvidence(model, elements, state);
  expect(model.groups[0]?.modules[1]?.inCone).toBeUndefined();
});
