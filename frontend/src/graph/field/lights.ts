// Which nodes the dot field lights, in which color and with what weight: DESIGN.md's tables of
// "Dot field and rings", one function per mode. It is a pure selector over app state, so the canvas
// has no per-node facts of its own and only draws what it is given. Every weight is a theme token.
import type { Slot } from "@contracts";

import { selectPendingChanges } from "../../state";
import type { ChangeState, State } from "../../state";
import { getActiveTheme } from "../../theme";

/** The field's four modes: watch, the live diff, the file view and review. */
export type FieldMode = "watch" | "live-diff" | "file" | "review";

/** What the person has open, as far as the field cares. The app derives it from its view. */
export type FieldScope =
  | { mode: "watch" }
  | { mode: "live-diff"; changeId: string }
  | { mode: "review"; changeId: string }
  | { mode: "file"; nodeId: string | null };

/** The theme token a light takes its color from: an agent's color, or the cone's neutral one. */
export type LightSource = "agent-a" | "agent-b" | "work-light";

export interface FieldLight {
  id: string;
  source: LightSource;
  /** Before the mode's gain: 1 is the strongest light the mode draws. */
  weight: number;
  /** Breathes and sends rings: only the editing node of an agent at work, in watch and live diff. */
  pulses: boolean;
  /** Rendering envelope during an eased transition; pure selectors leave this unset. */
  pulseWeight?: number;
}

/** The weights the tables give, from the theme. */
export interface LightWeights {
  "watch-editing-weight": number;
  "watch-changed-weight": number;
  "watch-read-weight": number;
  "review-changed-weight": number;
  "review-new-weight": number;
  "review-cone-near-weight": number;
  "review-cone-far-weight": number;
  "live-diff-editing-weight": number;
  "live-diff-read-weight": number;
  "live-diff-cone-weight": number;
  "file-view-selected-weight": number;
  "file-view-neighbor-weight": number;
}

const agentSource = (slot: Slot): LightSource => (slot === "A" ? "agent-a" : "agent-b");

/** One light per node and source; a node lit twice keeps its strongest weight. */
class Lights {
  private readonly byKey = new Map<string, FieldLight>();

  add(id: string, source: LightSource, weight: number, pulses = false) {
    const key = `${source}\u0000${id}`;
    const known = this.byKey.get(key);
    if (!known) {
      this.byKey.set(key, { id, source, weight, pulses });
    } else {
      known.weight = Math.max(known.weight, weight);
      known.pulses ||= pulses;
    }
  }

  /** Strongest first, then by source and id, so equal states give equal lists. */
  list(): FieldLight[] {
    return [...this.byKey.values()].sort(
      (a, b) => b.weight - a.weight || a.source.localeCompare(b.source) || a.id.localeCompare(b.id),
    );
  }
}

const atWork = (state: State["agents"][string]) =>
  state.state === "working" || state.state === "revising";

/** A paused agent is alive but shows no focus; every other state not at work is "done". */
const isDone = (state: State["agents"][string]) =>
  !atWork(state) && state.state !== "paused" && state.state !== "starting";

/** The nodes a change touches, in the order the panel's selection and the camera use. */
const changedIds = (change: ChangeState): string[] => [
  ...change.graph.nodes_added.map((node) => node.id),
  ...change.graph.nodes_modified,
  ...change.graph.nodes_removed,
];

/** The change's first node of its first file: the one that stands for it once its agent is done. */
function mainChangedNode(change: ChangeState): string | undefined {
  const path = Object.keys(change.files).sort()[0];
  return (
    (path === undefined ? undefined : change.files[path]?.node_ids[0]) ?? changedIds(change)[0]
  );
}

/** Each agent in its color: editing 1.0, other changed nodes 0.72, read nodes 0.36. */
function watchLights(state: State, weights: LightWeights): Lights {
  const lights = new Lights();
  const pending = selectPendingChanges(state);
  for (const agent of Object.values(state.agents)) {
    const source = agentSource(agent.slot);
    const focus = state.focus[agent.agent_id];
    const working = atWork(agent);
    for (const change of pending.filter((candidate) => candidate.agent_id === agent.agent_id)) {
      const main = isDone(agent) ? mainChangedNode(change) : undefined;
      for (const id of changedIds(change)) {
        lights.add(
          id,
          source,
          id === main ? weights["watch-editing-weight"] : weights["watch-changed-weight"],
        );
      }
    }
    if (!working || !focus) continue;
    const weight =
      focus.mode === "edit" ? weights["watch-editing-weight"] : weights["watch-read-weight"];
    for (const id of focus.node_ids) lights.add(id, source, weight, focus.mode === "edit");
  }
  return lights;
}

/** The change in its agent's color and the cone in work-light, nothing the agent only read. */
function reviewLights(state: State, changeId: string, weights: LightWeights): Lights {
  const lights = new Lights();
  const change = state.changes[changeId];
  const agent = change ? state.agents[change.agent_id] : undefined;
  if (!change || !agent) return lights;
  const source = agentSource(agent.slot);
  for (const node of change.graph.nodes_added)
    lights.add(node.id, source, weights["review-new-weight"]);
  for (const id of [...change.graph.nodes_modified, ...change.graph.nodes_removed])
    lights.add(id, source, weights["review-changed-weight"]);
  for (const entry of change.proposal?.impact.entries ?? []) {
    lights.add(
      entry.node_id,
      "work-light",
      entry.distance <= 1 ? weights["review-cone-near-weight"] : weights["review-cone-far-weight"],
    );
  }
  return lights;
}

/** The change's agent: its editing node 1.0, its read nodes 0.4, and the cone so far at 0.48. */
function liveDiffLights(state: State, changeId: string, weights: LightWeights): Lights {
  const lights = new Lights();
  const change = state.changes[changeId];
  const agent = change ? state.agents[change.agent_id] : undefined;
  if (!change || !agent) return lights;
  const focus = state.focus[agent.agent_id];
  if (focus && atWork(agent)) {
    const editing = focus.mode === "edit";
    const weight = editing ? weights["live-diff-editing-weight"] : weights["live-diff-read-weight"];
    for (const id of focus.node_ids) lights.add(id, agentSource(agent.slot), weight, editing);
  }
  // The proposal's cone is the one the server computed; a change still being edited has none yet.
  for (const entry of change.proposal?.impact.entries ?? [])
    lights.add(entry.node_id, "work-light", weights["live-diff-cone-weight"]);
  return lights;
}

/** The selected node 0.8 and the nodes that call it or that it calls 0.32, all in work-light. */
function fileLights(state: State, nodeId: string | null, weights: LightWeights): Lights {
  const lights = new Lights();
  if (nodeId === null) return lights;
  lights.add(nodeId, "work-light", weights["file-view-selected-weight"]);
  for (const edge of state.graph?.edges ?? []) {
    if (edge.kind !== "calls") continue;
    if (edge.src === nodeId && edge.dst !== nodeId)
      lights.add(edge.dst, "work-light", weights["file-view-neighbor-weight"]);
    if (edge.dst === nodeId && edge.src !== nodeId)
      lights.add(edge.src, "work-light", weights["file-view-neighbor-weight"]);
  }
  return lights;
}

/** The lights for what the person has open, strongest first. */
export function selectLights(
  state: State,
  scope: FieldScope,
  weights: LightWeights = getActiveTheme(),
): FieldLight[] {
  switch (scope.mode) {
    case "watch":
      return watchLights(state, weights).list();
    case "review":
      return reviewLights(state, scope.changeId, weights).list();
    case "live-diff":
      return liveDiffLights(state, scope.changeId, weights).list();
    case "file":
      return fileLights(state, scope.nodeId, weights).list();
    default: {
      const exhaustive: never = scope;
      return exhaustive;
    }
  }
}

/** True when two selections would draw the same field, so an event that changes neither skips a redraw. */
export function sameLights(a: readonly FieldLight[], b: readonly FieldLight[]): boolean {
  return (
    a.length === b.length &&
    a.every((light, index) => {
      const other = b[index];
      return (
        other !== undefined &&
        light.id === other.id &&
        light.source === other.source &&
        light.weight === other.weight &&
        light.pulses === other.pulses
      );
    })
  );
}
