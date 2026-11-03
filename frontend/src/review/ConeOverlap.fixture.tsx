// Hand-written accepted events; shared by projection, mounted review and capture proofs.
import type { ChangeGraph, ChangeResolved, ServerEvent } from "@contracts";
import { applyEvent, createInitialState, stateStore } from "../state";
import { nodeId, parameterState } from "./NewParameterCalls.fixture";

export const coneStates = [
  "no-peers",
  "intersection",
  "disjoint",
  "missing-graph",
  "partial-graph",
  "merged",
  "rejected",
  "stopped",
  "revision-added",
  "revision-removed",
  "several-peers",
  "self-only",
  "replay-prefix",
] as const;
export type ConeState = (typeof coneStates)[number];
export const callerId = "shop.cart:Cart.quote_add";
export const emptyGraph = (): ChangeGraph => ({
  nodes_added: [],
  nodes_modified: [],
  nodes_removed: [],
  edges_added: [],
  edges_removed: [],
});
type EventInput = {
  [T in ServerEvent["type"]]: Pick<Extract<ServerEvent, { type: T }>, "type" | "payload">;
}[ServerEvent["type"]];
export function stamp(inputs: EventInput[], after = 0): ServerEvent[] {
  return inputs.map((input, index) => ({
    ...input,
    seq: after + index + 1,
    ts: new Date(Date.UTC(2026, 10, 3, 12, 0, after + index)).toISOString(),
  }));
}
export function resolution(decision: ChangeResolved["decision"], stopped = false): ChangeResolved {
  return {
    change_id: "peer",
    agent_id: "peer-agent",
    revision: 1,
    decision,
    reason: stopped ? "stopped" : null,
    comment: null,
    merge: decision === "approved" ? "merged" : null,
    merge_commit: decision === "approved" ? "merged-head" : null,
    conflict: null,
    decided_by: "person",
    review_seq: null,
    requested_by: null,
    head_commit: "peer-head",
    reasons: [],
    assessment: null,
    answers: null,
  };
}
export function coneEvents(name: ConeState = "intersection"): ServerEvent[] {
  const base = parameterState("mixed");
  const proposal = base.changes["change-1"]?.proposal;
  const sourceAgent = base.agents["agent-1"];
  const agent = sourceAgent
    ? {
        ...sourceAgent,
        plan_id: sourceAgent.plan_id ?? null,
        zone: sourceAgent.zone ?? null,
        started_by: sourceAgent.started_by ?? null,
        queued_task_id: sourceAgent.queued_task_id ?? null,
        base_commit: sourceAgent.base_commit ?? null,
        examples: sourceAgent.examples ?? null,
        answering: sourceAgent.answering ?? null,
      }
    : undefined;
  const facts = base.changeFacts["change-1"]?.at(-1);
  if (!base.run || !base.graph || !proposal || !agent || !facts) throw new Error("Missing shell");
  const peerGraph = {
    ...emptyGraph(),
    nodes_modified: name === "disjoint" ? ["shop.pricing:shipping_cost"] : [nodeId],
  };
  const inputs: EventInput[] = [
    { type: "run.started", payload: base.run },
    ...(name === "missing-graph"
      ? []
      : [
          {
            type: "graph.snapshot" as const,
            payload: {
              ...base.graph,
              parse_errors:
                name === "partial-graph"
                  ? [{ path: "shop/partial.py", message: "Syntax error", line: 1 }]
                  : [],
            },
          },
        ]),
    { type: "agent.status", payload: agent },
    { type: "change.proposed", payload: proposal },
    { type: "change.facts_recorded", payload: facts },
  ];
  if (name !== "no-peers") {
    const self = name === "self-only";
    inputs.push(
      {
        type: "agent.status",
        payload: {
          ...agent,
          agent_id: self ? agent.agent_id : "peer-agent",
          change_id: self ? "change-1" : "peer",
          slot: self ? "A" : "B",
          state: self ? "ready_for_review" : "paused",
        },
      },
      {
        type: "change.proposed",
        payload: {
          ...proposal,
          change_id: "peer",
          agent_id: self ? agent.agent_id : "peer-agent",
          graph: peerGraph,
          head_commit: "peer-head",
        },
      },
    );
  }
  if (name === "several-peers") {
    for (const [index, state] of ["halted", "revising"].entries()) {
      const id = `peer-${String(index)}`;
      inputs.push(
        {
          type: "agent.status",
          payload: {
            ...agent,
            agent_id: id,
            change_id: id,
            slot: index ? "D" : "C",
            state: state as "halted" | "revising",
          },
        },
        {
          type: "change.proposed",
          payload: { ...proposal, agent_id: id, change_id: id, graph: peerGraph },
        },
      );
    }
  }
  if (["merged", "rejected", "stopped"].includes(name))
    inputs.push({
      type: "change.resolved",
      payload: resolution(name === "merged" ? "approved" : "rejected", name === "stopped"),
    });
  if (name === "revision-added" || name === "revision-removed")
    inputs.push({
      type: "change.proposed",
      payload: {
        ...proposal,
        change_id: "peer",
        agent_id: "peer-agent",
        revision: 2,
        graph: {
          ...peerGraph,
          nodes_modified: name === "revision-added" ? [nodeId, callerId] : [callerId],
        },
      },
    });
  return stamp(inputs);
}
export const coneState = (name: ConeState) =>
  coneEvents(name).reduce(applyEvent, createInitialState());

/** Dynamic import keeps the DOM harness out of unit fixtures and the shipped bundle. */
export const coneHarness = {
  async mount(name: ConeState) {
    const { paramEvidence } = await import("./paramEvidence.browser.fixture");
    await paramEvidence.mount("mixed");
    await this.replace(name);
  },
  async replace(name: ConeState) {
    if (name === "replay-prefix") {
      const { startReplay } = await import("../transport");
      const replay = startReplay({
        store: stateStore,
        speed: 0,
        source: {
          text: coneEvents("revision-added")
            .map((event) => JSON.stringify(event))
            .join("\n"),
        },
      });
      await replay.ready;
      replay.stop();
      return;
    }
    const { flushSync } = await import("react-dom");
    flushSync(() => {
      stateStore.setState({ state: coneState(name) });
    });
  },
  async evidence(facts: boolean, comparison: boolean) {
    const state = structuredClone(stateStore.getState().state);
    if (!facts) state.changeFacts = {};
    else {
      const current = state.changeFacts["change-1"]?.at(-1);
      if (current && !comparison) {
        current.behavior_diff = null;
        current.measured_cone = null;
        current.review_cards = null;
      }
    }
    const { flushSync } = await import("react-dom");
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  async stress() {
    const state = structuredClone(stateStore.getState().state);
    const node = state.graph?.nodes.find((node) => node.id === nodeId);
    if (node) node.qualname = "<script>" + "Long_函数_".repeat(40);
    const { flushSync } = await import("react-dom");
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
};
declare global {
  interface Window {
    coneHarness: typeof coneHarness;
  }
}
