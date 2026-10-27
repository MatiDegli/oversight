import type { AgentStatus, ChangeUpdated, ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";

import { appendEvent, panelEvents, through } from "./panels.fixture";
import { selectAgentCard, selectGraphSummary, selectLiveDiff, selectNodeMarks } from "./selectors";
import { createChange, createInitialState } from "./types";

describe("panel selectors over the level 1 fixture", () => {
  it("selects running, every finished step and the ready pill without mutating state", () => {
    expect(selectAgentCard(createInitialState(), "agent-1")).toBeUndefined();
    expect(selectAgentCard(through(4), "agent-1")?.agent.state).toBe("working");
    expect(selectAgentCard(through(4), "agent-1")?.steps).toEqual([]);
    for (const event of panelEvents) {
      if (event.type !== "agent.step") continue;
      const state = through(event.seq);
      const before = structuredClone(state);
      Object.freeze(state.steps[event.payload.agent_id]);
      const card = selectAgentCard(state, event.payload.agent_id);
      expect(card?.steps[0]).toEqual(event.payload);
      expect(card?.step).toBe(event.payload.step);
      expect(card?.steps.map((entry) => entry.step)).toEqual(
        Array.from({ length: event.payload.step }, (_, index) => event.payload.step - index),
      );
      expect(state).toEqual(before);
    }
    expect(selectAgentCard(through(19), "agent-1")?.readyForReview).toBe(false);
    expect(selectAgentCard(through(20), "agent-1")?.readyForReview).toBe(true);
    expect(selectAgentCard(through(23), "agent-1")?.readyForReview).toBe(false);
  });

  it("selects full contents by change and selected file as subsequent edits arrive", () => {
    const event = panelEvents.find((event) => event.type === "change.updated");
    expect(event).toBeDefined();
    if (!event) throw new Error("Fixture needs change.updated");
    const { original, modified, path } = event.payload.file;
    expect(selectLiveDiff(through(11), "change-1", path)).toBeUndefined();
    const first = through(12);
    const firstInput = selectLiveDiff(first, "change-1", path);
    expect(firstInput).toMatchObject({ original, modified, path, author: "agent" });
    const update = (payload: ChangeUpdated): ServerEvent => ({ ...event, payload });
    const second = appendEvent(
      first,
      update({
        ...event.payload,
        file: {
          ...event.payload.file,
          path: "shop/other.py",
          original: null,
          modified: "new file",
        },
      }),
    );
    const third = appendEvent(
      second,
      update({
        ...event.payload,
        author: "user",
        file: { ...event.payload.file, modified: "latest complete file", lines_added: 3 },
      }),
    );
    expect(selectLiveDiff(third, "change-1", path)).toMatchObject({
      original,
      modified: "latest complete file",
      author: "user",
      linesAdded: 3,
    });
    expect(selectLiveDiff(third, "change-1", "shop/other.py")).toMatchObject({
      original: "",
      modified: "new file",
    });
    expect(selectLiveDiff(first, "change-1", path)).toEqual(firstInput);
    expect(selectLiveDiff(third, "missing", path)).toBeUndefined();
    expect(selectLiveDiff(third, "change-1", "missing.py")).toBeUndefined();
    const otherAgent = appendEvent(
      third,
      update({ ...event.payload, agent_id: "agent-2", change_id: "change-2" }),
    );
    expect(selectLiveDiff(otherAgent, "change-2", path)?.modified).toBe(modified);
    expect(selectLiveDiff(otherAgent, "change-1", path)?.modified).toBe("latest complete file");
    const deleted = appendEvent(
      otherAgent,
      update({
        ...event.payload,
        file: { ...event.payload.file, status: "deleted", modified: null },
      }),
    );
    expect(selectLiveDiff(deleted, "change-1", path)?.modified).toBe("");
  });
});

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

describe("selectGraphSummary", () => {
  it("counts modules and functions of main, tests left out, and the agents at work", () => {
    expect(selectGraphSummary(createInitialState())).toEqual({
      repo: null,
      modules: 0,
      functions: 0,
      working: 0,
    });
    // The fixture's snapshot: 6 modules, 14 functions and 22 methods, 8 classes not counted.
    expect(selectGraphSummary(through(2))).toEqual({
      repo: "demo-repo",
      modules: 6,
      functions: 36,
      working: 0,
    });
    expect(selectGraphSummary(through(4)).working).toBe(1);
    expect(selectGraphSummary(through(20)).working).toBe(0);
  });

  it("leaves a test module and its functions out", () => {
    const state = through(2);
    const test = { path: "tests/t.py", start_line: 1, end_line: 2, parent_id: null, is_test: true };
    const graph = {
      ...state.graph,
      nodes: [
        ...(state.graph?.nodes ?? []),
        { ...test, id: "tests.t", kind: "module", name: "t", qualname: "tests.t" },
        { ...test, id: "tests.t:f", kind: "function", name: "f", qualname: "f" },
      ],
    } as NonNullable<typeof state.graph>;
    expect(selectGraphSummary({ ...state, graph })).toEqual(selectGraphSummary(state));
  });
});
