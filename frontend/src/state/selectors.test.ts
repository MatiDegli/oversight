import type { AgentStatus } from "@contracts";
import { describe, expect, it } from "vitest";

import { selectNodeMarks } from "./selectors";
import { createChange, createInitialState } from "./types";

const agent = (slot: "A" | "B", state: AgentStatus["state"] = "working"): AgentStatus => ({
  agent_id: slot,
  change_id: slot,
  slot,
  state,
  task: "Test",
  model: "fake",
  step: 0,
  max_steps: 10,
  detail: null,
});
const focused = () => ({
  ...createInitialState(),
  agents: { A: agent("A"), B: agent("B", "revising") },
  focus: {
    A: { agent_id: "A", mode: "read" as const, node_ids: ["f"], ranges: [] },
    B: { agent_id: "B", mode: "edit" as const, node_ids: ["f", "g"], ranges: [] },
  },
});

describe("selectNodeMarks", () => {
  it("returns independent focus modes per slot only while working or revising", () => {
    const state = focused();
    const before = JSON.stringify(state);
    expect(selectNodeMarks(state)).toEqual({
      f: { pending: false, pendingSlots: [], focus: { A: "read", B: "edit" } },
      g: { pending: false, pendingSlots: [], focus: { B: "edit" } },
    });
    for (const status of [
      "starting",
      "ready_for_review",
      "done",
      "stopped",
      "halted",
      "paused",
    ] as const) {
      expect(selectNodeMarks({ ...state, agents: { A: agent("A", status) } })).toEqual({});
    }
    expect(JSON.stringify(state)).toBe(before);
  });

  it("deduplicates pending slots on additions, modifications and removals without mutating state", () => {
    const state = focused();
    const first = createChange("a", "A");
    first.graph.nodes_modified = ["f", "f"];
    first.graph.nodes_removed = ["g"];
    const second = createChange("b", "B");
    second.graph.nodes_modified = ["f"];
    state.changes = { b: second, a: first };
    const before = JSON.stringify(state);
    expect(selectNodeMarks(state).f).toEqual({
      pending: true,
      pendingSlots: ["A", "B"],
      focus: { A: "read", B: "edit" },
    });
    expect(selectNodeMarks(state).g?.pendingSlots).toEqual(["A"]);
    expect(JSON.stringify(state)).toBe(before);
    for (const status of ["approved", "rejected"] as const) {
      expect(selectNodeMarks({ ...state, changes: { a: { ...first, status } } }).f?.pending).toBe(
        false,
      );
    }
  });

  it("keeps an added node pending when its agent is unknown", () => {
    const change = createChange("unknown", "missing");
    change.graph.nodes_added = [
      {
        id: "new",
        name: "new",
        qualname: "new",
        kind: "function",
        parent_id: null,
        path: "new.py",
        start_line: 1,
        end_line: 2,
        is_test: false,
      },
    ];
    const state = { ...createInitialState(), changes: { unknown: change } };
    expect(selectNodeMarks(state)).toEqual({ new: { pending: true, pendingSlots: [], focus: {} } });
  });
});
