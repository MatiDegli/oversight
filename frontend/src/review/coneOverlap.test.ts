import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { applyEvent, createInitialState, createStateStore, type State } from "../state";
import { presentationFor } from "../teams/presentation";
// eslint-disable-next-line no-restricted-imports -- Exercise the actual replay seek implementation.
import { createReplayPlayer } from "../transport/replayPlayer";
import { ConeOverlap } from "./ConeOverlap";
import {
  coneEvents,
  coneState,
  emptyGraph,
  resolution,
  stamp,
  callerId,
} from "./ConeOverlap.fixture";
import { nodeId } from "./NewParameterCalls.fixture";
import { coneOverlap } from "./coneOverlap";

function required<T>(value: T | undefined | null): T {
  if (value == null) throw new Error("Missing fixture value");
  return value;
}
const project = (state: State) => coneOverlap(state, "change-1");
const html = (state: State) =>
  renderToStaticMarkup(createElement(ConeOverlap, { state, changeId: "change-1" }));
function changes(state: State) {
  const reviewed = state.changes["change-1"],
    peer = state.changes.peer;
  if (!reviewed || !peer) throw new Error("Missing changes");
  return { reviewed, peer };
}
it.each(["no-peers", "merged", "rejected", "stopped", "self-only"] as const)(
  "omits resolved and self peers: %s",
  (name) => {
    expect(project(coneState(name))).toEqual([]);
    expect(html(coneState(name))).toBe("");
  },
);
it("intersects deduplicated reviewed additions, modifications and removals with seeds and callers", () => {
  const state = coneState("intersection"),
    { reviewed, peer } = changes(state);
  reviewed.graph = { ...emptyGraph(), nodes_modified: [nodeId, nodeId], nodes_removed: [callerId] };
  expect(project(state)[0]?.nodes.map((node) => node.id)).toEqual([callerId, nodeId].sort());
  const node = state.graph?.nodes.find((node) => node.id === nodeId);
  if (!node) throw new Error("Missing node");
  reviewed.graph.nodes_added = [{ ...node, qualname: "Current.added" }];
  expect(project(state)[0]?.nodes.find((node) => node.id === nodeId)?.name).toBe("Current.added");
  peer.graph.nodes_modified = ["missing"];
  expect(project(state)[0]?.limitations).toContain("missing-seeds");
  expect(project(state)[0]?.nodes).toEqual([]);
});
it("retains observed matches alongside absent seeds without mutating accepted state", () => {
  const state = coneState("intersection");
  changes(state).peer.graph.nodes_modified.push("missing");
  const before = JSON.stringify(state);
  expect(project(state)[0]).toMatchObject({
    nodes: [{ id: nodeId, name: "compute_price" }],
    limitations: ["missing-seeds"],
  });
  expect(presentationFor(state, "peer-agent")?.kind).toBe("agent");
  expect(html(state)).toContain("absent from the graph");
  expect(JSON.stringify(state)).toBe(before);
});
it("keeps absent names lossless and never invents removed peer seeds", () => {
  const state = coneState("intersection"),
    { reviewed, peer } = changes(state);
  const node = state.graph?.nodes.find((node) => node.id === nodeId);
  if (node) node.qualname = "";
  expect(project(state)[0]?.nodes[0]?.name).toBe(nodeId);
  peer.graph = { ...emptyGraph(), nodes_removed: [nodeId] };
  reviewed.graph.nodes_removed = [nodeId];
  expect(project(state)[0]).toMatchObject({ nodes: [], limitations: ["removed-only"] });
  expect(html(state)).toContain("removed nodes do not seed");
});
it("distinguishes unavailable inputs, bounded empty results and retained partial matches", () => {
  const missing = coneState("missing-graph");
  expect(html(missing)).toContain("Cannot check Agent B");
  expect(html(missing)).not.toContain("No edited nodes");
  const noRun = coneState("intersection");
  noRun.run = null;
  expect(project(noRun)[0]?.unavailable).toBe("run limits");
  const partial = coneState("partial-graph");
  expect(project(partial)[0]?.nodes).toHaveLength(1);
  expect(html(partial)).toContain("last good parses");
  changes(partial).reviewed.graph.nodes_modified = ["unrelated"];
  expect(project(partial)[0]?.nodes).toEqual([]);
  expect(html(partial)).toContain("currently computed cone");
  partial.gap = true;
  if (partial.run) partial.run.limits.cone_max_nodes = 0;
  expect(project(partial)[0]?.limitations).toEqual([
    "parse-errors",
    "sequence-gap",
    "cone-omissions",
  ]);
  expect(project(coneState("disjoint"))[0]).toMatchObject({
    nodes: [],
    unavailable: null,
    limitations: [],
  });
});
it.each(["paused", "halted", "revising"] as const)(
  "retains %s agents and pending conflicts or revisions",
  (status) => {
    let state = coneState("intersection");
    const agent = state.agents["peer-agent"];
    if (agent) agent.state = status;
    for (const outcome of [
      resolution("changes_requested"),
      {
        ...resolution("approved"),
        merge: "conflict" as const,
        merge_commit: null,
        conflict: { kind: "merge" as const, paths: [], tests: null },
      },
    ]) {
      state = applyEvent(
        state,
        required(stamp([{ type: "change.resolved", payload: outcome }], state.lastSeq)[0]),
      );
      expect(project(state)).toHaveLength(1);
    }
  },
);
it("replaces reviewed and peer revisions, including current removed reviewed edits", () => {
  let state = coneState("intersection");
  const proposal = required(changes(state).reviewed.proposal);
  const replace = (id: string, modified: string[], removed: string[] = []) => {
    state = applyEvent(
      state,
      required(
        stamp(
          [
            {
              type: "change.proposed",
              payload: {
                ...proposal,
                change_id: id,
                agent_id: id === "peer" ? "peer-agent" : "agent-1",
                revision: 2,
                graph: { ...emptyGraph(), nodes_modified: modified, nodes_removed: removed },
              },
            },
          ],
          state.lastSeq,
        )[0],
      ),
    );
  };
  replace("change-1", [callerId]);
  expect(project(state)[0]?.nodes.map((node) => node.id)).toEqual([callerId]);
  replace("change-1", [], [nodeId]);
  expect(project(state)[0]?.nodes.map((node) => node.id)).toEqual([nodeId]);
  replace("peer", [callerId]);
  expect(project(state)[0]?.nodes).toEqual([]);
  replace("peer", [nodeId]);
  expect(project(state)[0]?.nodes.map((node) => node.id)).toEqual([nodeId]);
  const peer = changes(state).peer;
  state = applyEvent(
    state,
    required(
      stamp(
        [
          {
            type: "change.updated",
            payload: {
              change_id: "peer",
              agent_id: "peer-agent",
              author: "agent",
              file: {
                path: "shop/pricing.py",
                status: "modified",
                original: "",
                modified: "",
                lines_added: 0,
                lines_removed: 0,
              },
              node_ids: [],
              graph: emptyGraph(),
              files_changed: 1,
              lines_added: 0,
              lines_removed: 0,
            },
          },
        ],
        state.lastSeq,
      )[0],
    ),
  );
  expect(project(state)[0]?.nodes).toEqual([]);
  expect(peer.graph.nodes_modified).toEqual([nodeId]);
});
it("uses accepted main deltas and drops removed nodes and their dangling caller membership", () => {
  let state = coneState("intersection");
  changes(state).reviewed.graph.nodes_removed = [callerId];
  expect(project(state)[0]?.nodes).toHaveLength(2);
  const graph = required(state.graph);
  state = applyEvent(
    state,
    required(
      stamp(
        [
          {
            type: "graph.delta",
            payload: {
              from_version: graph.version,
              to_version: "next",
              commit: graph.commit,
              nodes_added: [],
              nodes_changed: [],
              nodes_removed: [callerId],
              edges_added: [],
              edges_changed: [],
              edges_removed: [],
              parse_errors: [],
              cause: "user_edit",
            },
          },
        ],
        state.lastSeq,
      )[0],
    ),
  );
  expect(project(state)[0]?.nodes.map((node) => node.id)).toEqual([nodeId]);
});
it.each([null, "lead"])(
  "keeps same-team members distinct with plan/lead fallbacks (%s)",
  (lead) => {
    let state = coneState("several-peers");
    const agents = ["agent-1", "peer-agent", "peer-0"];
    const events = stamp(
      [
        {
          type: "plan.updated",
          payload: {
            plan_id: "plan",
            revision: 1,
            task: "Shared plan",
            graph_version: "v1",
            state: "running",
            detail: null,
            started_by: lead,
            queued_task_id: null,
            subtasks: agents.map((agent_id) => ({
              task: "Edit",
              zone: { node_ids: [], paths: [] },
              depends_on: [],
              agent_id,
              state: "running",
            })),
          },
        },
        ...agents.map((id) => ({
          type: "agent.status" as const,
          payload: { ...required(state.agents[id]), plan_id: "plan" },
        })),
      ],
      state.lastSeq,
    );
    state = events.reduce(applyEvent, state);
    const own = presentationFor(state, "agent-1"),
      peer = presentationFor(state, "peer-agent");
    expect([peer?.team, peer?.hue]).toEqual([own?.team, own?.hue]);
    expect(peer?.kind).toBe(lead ? "lead" : "plan");
    expect(peer?.label).toBe("A·2");
    expect(project(state)).toHaveLength(3);
    expect(html(state)).toContain("Agent A·2");
    expect(project(state).map((peer) => peer.changeId)).toEqual(["peer", "peer-0", "peer-1"]);
  },
);
it("matches live application and parsed replay, seeking both directions across revision and resolution", async () => {
  const events = coneEvents("revision-removed");
  events.push(
    ...stamp([{ type: "change.resolved", payload: resolution("rejected", true) }], events.length),
  );
  const live = createStateStore(),
    prefixes = [createInitialState()];
  for (const event of events) {
    live.getState().applyEvents([event]);
    prefixes.push(live.getState().state);
  }
  const store = createStateStore();
  const player = createReplayPlayer({
    store,
    source: { text: events.map((event) => JSON.stringify(event)).join("\n") },
  });
  try {
    await player.ready;
    player.pause();
    for (const index of [...events.keys(), ...events.keys()].map((index, i) =>
      i < events.length ? index : events.length - index - 1,
    )) {
      const event = required(events[index]);
      player.seekSeq(event.seq);
      const accepted = required(prefixes[index + 1]);
      expect(project(store.getState().state)).toEqual(project(accepted));
      expect(html(store.getState().state)).toBe(html(accepted));
    }
  } finally {
    player.dispose();
  }
});
