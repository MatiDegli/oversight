import { readFileSync } from "node:fs";

import type { AgentFocus, AgentStatus, GraphEdge, ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState } from "../../state";
import type { State } from "../../state";
import { themes } from "../../theme";
import { sameLights, selectLights } from "./lights";
import type { FieldLight } from "./lights";

const events = readFileSync(
  new URL("../../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

/** The recorded run up to an event: agent 1 is Agent A, and its change is change-1. */
function through(seq: number): State {
  return structuredClone(
    events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState()),
  );
}

const CHANGED = "shop.pricing:compute_price";
const OTHER = "shop.pricing:price_breaks";

function agentOf(state: State, id: string): AgentStatus {
  const agent = state.agents[id];
  if (!agent) throw new Error(`The log has no ${id}`);
  return agent;
}

function changeOf(state: State) {
  const change = state.changes["change-1"];
  if (!change) throw new Error("The log has no change-1");
  return change;
}

/** Agent B at work on its own change, with the focus the test gives it. */
function addAgentB(state: State, focus: Pick<AgentFocus, "mode" | "node_ids">) {
  state.agents["agent-2"] = {
    ...agentOf(state, "agent-1"),
    agent_id: "agent-2",
    slot: "B",
    change_id: "change-2",
    state: "working",
  };
  state.focus["agent-2"] = { agent_id: "agent-2", ranges: [], ...focus };
}

const summary = (lights: FieldLight[]) =>
  lights.map(({ id, source, weight, pulses }) => [id, source, weight, pulses]);

describe("watch lights", () => {
  it("lights each agent in its color: editing 1.0 with its pulse, changed 0.72, read 0.36", () => {
    const state = through(13);
    changeOf(state).graph.nodes_modified = [CHANGED, OTHER];
    addAgentB(state, { mode: "read", node_ids: ["shop.cart:Cart.add"] });
    expect(agentOf(state, "agent-1").state).toBe("working");
    expect(state.focus["agent-1"]?.mode).toBe("edit");
    expect(summary(selectLights(state, { mode: "watch" }))).toEqual([
      [CHANGED, "agent-a", 1, true],
      [OTHER, "agent-a", 0.72, false],
      ["shop.cart:Cart.add", "agent-b", 0.36, false],
    ]);
  });

  it("lights an agent's main changed node at 1.0, still, once the agent is done", () => {
    const state = through(20);
    expect(agentOf(state, "agent-1").state).toBe("ready_for_review");
    changeOf(state).graph.nodes_modified = [OTHER, CHANGED];
    // The change's first file names the node its edit was at; that one stands for the change.
    const file = Object.values(changeOf(state).files)[0];
    expect(file?.node_ids[0]).toBe(CHANGED);
    expect(summary(selectLights(state, { mode: "watch" }))).toEqual([
      [CHANGED, "agent-a", 1, false],
      [OTHER, "agent-a", 0.72, false],
    ]);
  });

  it("lights nothing for the reads of an agent that is not at work, or for a decided change", () => {
    const reading = through(9);
    expect(summary(selectLights(reading, { mode: "watch" }))).toEqual([
      [CHANGED, "agent-a", 0.36, false],
    ]);
    reading.agents["agent-1"] = { ...agentOf(reading, "agent-1"), state: "ready_for_review" };
    expect(selectLights(reading, { mode: "watch" })).toEqual([]);
    // Event 21 approves the change: nothing is pending any more.
    expect(selectLights(through(21), { mode: "watch" })).toEqual([]);
  });

  it("lights a node both agents touch once in each color", () => {
    const state = through(13);
    addAgentB(state, { mode: "edit", node_ids: [CHANGED] });
    expect(summary(selectLights(state, { mode: "watch" }))).toEqual([
      [CHANGED, "agent-a", 1, true],
      [CHANGED, "agent-b", 1, true],
    ]);
  });

  it("takes every weight from the theme", () => {
    const state = through(13);
    changeOf(state).graph.nodes_modified = [CHANGED, OTHER];
    const lights = selectLights(
      state,
      { mode: "watch" },
      {
        ...themes.dark,
        "watch-editing-weight": 0.9,
        "watch-changed-weight": 0.4,
      },
    );
    expect(summary(lights)).toEqual([
      [CHANGED, "agent-a", 0.9, true],
      [OTHER, "agent-a", 0.4, false],
    ]);
  });
});

describe("review lights", () => {
  const scope = { mode: "review", changeId: "change-1" } as const;

  it("lights the changed nodes at 1.0 and the new ones at 0.8 in the agent's color", () => {
    const state = through(20);
    const added = { ...(state.graph?.nodes[0] ?? failure()), id: "shop.pricing:tier_rate" };
    changeOf(state).graph.nodes_added = [added];
    const own = selectLights(state, scope).filter((light) => light.source === "agent-a");
    expect(summary(own)).toEqual([
      [CHANGED, "agent-a", 1, false],
      ["shop.pricing:tier_rate", "agent-a", 0.8, false],
    ]);
  });

  it("lights the cone in work-light: distance 1 at 0.52, farther at 0.38", () => {
    const state = through(20);
    const entries = changeOf(state).proposal?.impact.entries ?? [];
    expect(entries.some((entry) => entry.distance === 1)).toBe(true);
    expect(entries.some((entry) => entry.distance > 1)).toBe(true);
    const cone = selectLights(state, scope).filter((light) => light.source === "work-light");
    expect(cone).toHaveLength(entries.length);
    for (const light of cone) {
      const distance = entries.find((entry) => entry.node_id === light.id)?.distance ?? 0;
      expect(light.weight, light.id).toBe(distance === 1 ? 0.52 : 0.38);
    }
  });

  it("does not light read nodes, and holds still", () => {
    const state = through(20);
    state.agents["agent-1"] = { ...agentOf(state, "agent-1"), state: "working" };
    state.focus["agent-1"] = {
      agent_id: "agent-1",
      mode: "read",
      ranges: [],
      node_ids: ["shop.cart:Cart.add"],
    };
    const lights = selectLights(state, scope);
    expect(lights.map((light) => light.id)).not.toContain("shop.cart:Cart.add");
    expect(lights.some((light) => light.pulses)).toBe(false);
    state.focus["agent-1"] = { agent_id: "agent-1", mode: "edit", ranges: [], node_ids: [CHANGED] };
    expect(selectLights(state, scope).some((light) => light.pulses)).toBe(false);
  });

  it("lights nothing for a change that is not on the graph", () => {
    expect(selectLights(through(20), { mode: "review", changeId: "change-9" })).toEqual([]);
  });
});

describe("live diff lights", () => {
  const scope = { mode: "live-diff", changeId: "change-1" } as const;

  it("lights the agent's editing node at 1.0 with its pulse and its cone so far at 0.48", () => {
    // Event 19 proposes the change while the agent is still at work on it.
    const state = through(19);
    expect(agentOf(state, "agent-1").state).toBe("working");
    const lights = selectLights(state, { ...scope, cone: changeOf(state).proposal?.impact });
    const cone = changeOf(state).proposal?.impact.entries ?? [];
    expect(lights.filter((light) => light.source === "agent-a")).toEqual([
      { id: CHANGED, source: "agent-a", weight: 1, pulses: true },
    ]);
    expect(lights.filter((light) => light.source === "work-light")).toHaveLength(cone.length);
    expect(
      lights
        .filter((light) => light.source === "work-light")
        .every((light) => light.weight === 0.48),
    ).toBe(true);
  });

  it("lights the agent's read nodes at 0.4, still", () => {
    const state = through(9);
    expect(summary(selectLights(state, scope))).toEqual([[CHANGED, "agent-a", 0.4, false]]);
  });

  it("lights only this change's agent", () => {
    const state = through(13);
    addAgentB(state, { mode: "edit", node_ids: ["shop.cart:Cart.add"] });
    expect(selectLights(state, scope).map((light) => light.id)).toEqual([CHANGED]);
    expect(summary(selectLights(state, { mode: "live-diff", changeId: "change-2" }))).toEqual([]);
  });
});

describe("file view lights", () => {
  function edge(id: string, kind: GraphEdge["kind"], src: string, dst: string): GraphEdge {
    return { id, kind, src, dst, confidence: "exact", reason: null };
  }

  it("lights the selected node at 0.8 and its direct callers and callees at 0.32, in work-light", () => {
    const state = through(2);
    const graph = state.graph ?? failure();
    state.graph = {
      ...graph,
      edges: [
        edge("e1", "calls", "caller", CHANGED),
        edge("e2", "calls", CHANGED, "callee"),
        edge("e3", "calls", "far", "caller"),
        edge("e4", "imports", "importer", CHANGED),
        edge("e5", "inherits", CHANGED, "base"),
        edge("e6", "calls", CHANGED, CHANGED),
      ],
    };
    expect(summary(selectLights(state, { mode: "file", nodeId: CHANGED }))).toEqual([
      [CHANGED, "work-light", 0.8, false],
      ["callee", "work-light", 0.32, false],
      ["caller", "work-light", 0.32, false],
    ]);
  });

  it("lights nothing without a selected node", () => {
    expect(selectLights(through(2), { mode: "file", nodeId: null })).toEqual([]);
  });
});

describe("sameLights", () => {
  const light: FieldLight = { id: "a", source: "agent-a", weight: 1, pulses: true };

  it("tells equal selections apart from ones that would draw differently", () => {
    expect(sameLights([light], [{ ...light }])).toBe(true);
    expect(sameLights([light], [])).toBe(false);
    expect(sameLights([light], [{ ...light, weight: 0.72 }])).toBe(false);
    expect(sameLights([light], [{ ...light, pulses: false }])).toBe(false);
    expect(sameLights([light], [{ ...light, source: "agent-b" }])).toBe(false);
    expect(sameLights([light], [{ ...light, id: "b" }])).toBe(false);
  });
});

function failure(): never {
  throw new Error("The recorded run lacks what the test needs");
}
