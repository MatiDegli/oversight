import { describe, expect, expectTypeOf, it } from "vitest";

import type { State } from "../state";
import { graphScope } from "./graphScope";
import { fieldInput, fieldScope } from "./fieldScope";
import { through } from "./replay.fixture";
import { initialView } from "./view";
import type { View } from "./view";

const DIFF = { changeId: "change-1", path: "shop/pricing.py" };
const FILE = { path: "shop/pricing.py", agentId: null, startLine: 1, endLine: 20 };

const reviewing: View = { ...initialView, review: "change-1" };
const watching: View = { ...initialView, diff: DIFF };
const reading: View = { ...initialView, selected: "shop.pricing:compute_price", file: FILE };

describe("the field's scope follows what the person has open", () => {
  it("is watch with nothing open", () => {
    expect(fieldScope(through(12), initialView)).toEqual({ mode: "watch" });
    expect(fieldInput(through(12), initialView).mode).toBe("watch");
  });

  it("is review on the change in review, and live diff on the change in the live diff", () => {
    expect(fieldScope(through(20), reviewing)).toEqual({ mode: "review", changeId: "change-1" });
    expect(fieldScope(through(12), watching)).toEqual({ mode: "live-diff", changeId: "change-1" });
  });

  it("is the file view on the selected node", () => {
    expect(fieldScope(through(12), reading)).toEqual({
      mode: "file",
      nodeId: "shop.pricing:compute_price",
    });
    expect(fieldScope(through(12), { ...reading, selected: null })).toEqual({
      mode: "file",
      nodeId: null,
    });
  });

  it("goes back to watch once the change is decided, as the graph's dimming does", () => {
    expect(fieldScope(through(21), reviewing)).toEqual({ mode: "watch" });
    expect(fieldScope(through(21), watching)).toEqual({ mode: "watch" });
    expect(fieldInput(through(21), reviewing)).toEqual({ mode: "watch", lights: [] });
  });

  it("keeps the current agent signal in review and live diff", () => {
    const review = fieldInput(through(20), reviewing);
    expect(review.mode).toBe("review");
    expect(review.lights.map((light) => light.id)).toContain("shop.pricing:compute_price");
    expect(review.lights.every((light) => !light.pulses)).toBe(true);
    const live = fieldInput(through(12), watching);
    expect(live.mode).toBe("live-diff");
    expect(live.lights.map((light) => light.id)).toContain("shop.pricing:compute_price");
  });
});

it.each([14, 20])(
  "keeps the current live cone on the graph without emitting light at event %s",
  (seq) => {
    const state = through(seq);
    const change = state.changes["change-1"];
    const graph = state.graph;
    if (!change || !graph) throw new Error("Missing replay change");
    const seed = "shop.pricing:compute_price";
    const current = graph.nodes.find(
      (node) => node.id !== seed && !node.is_test && node.kind === "function",
    );
    if (!current) throw new Error("Missing caller");
    state.graph = {
      ...graph,
      edges: [
        {
          id: "current-call",
          kind: "calls",
          src: current.id,
          dst: seed,
          confidence: "exact",
          reason: null,
        },
      ],
    };
    change.status = seq === 20 ? "revising" : "editing";
    change.graph.edges_added = [];
    change.graph.edges_removed = [];
    const lights = fieldInput(state, watching).lights.filter(
      (light) => light.source === "work-light",
    );
    const graphView = graphScope(state, watching);
    expect(graphView.cone).toBeNull();
    expect(graphView.liveCone?.entries).toEqual([{ node_id: current.id, distance: 1 }]);
    expect(graphView.litIds).toContain(current.id);
    expect(lights).toEqual([]);
  },
);

it.each([initialView, watching, reviewing, reading])(
  "emits only the current and last read per agent regardless of the open view %j",
  (view) => {
    const state = through(13);
    const a = state.agents["agent-1"];
    if (!a) throw new Error("Missing agent");
    state.agents["agent-2"] = { ...a, agent_id: "agent-2", slot: "B", change_id: "change-2" };
    state.focus["agent-1"] = {
      agent_id: "agent-1",
      mode: "edit",
      ranges: [],
      node_ids: ["current-a", "extra-a"],
    };
    state.focus["agent-2"] = {
      agent_id: "agent-2",
      mode: "edit",
      ranges: [],
      node_ids: ["current-b", "extra-b"],
    };
    state.lastReads = { "agent-1": "last-a", "agent-2": "last-b" };
    const lights = fieldInput(state, view).lights;
    expect(lights.map(({ id, source, weight }) => [id, source, weight])).toEqual([
      ["current-a", "agent-a", 1],
      ["last-a", "agent-a", 0.4],
      ["current-b", "agent-b", 1],
      ["last-b", "agent-b", 0.4],
    ]);
    expect(lights.every((light) => light.steady)).toBe(true);
  },
);

it("takes only state and view, without a cone dependency", () => {
  expectTypeOf<Parameters<typeof fieldInput>>().toEqualTypeOf<[State, View]>();
});
