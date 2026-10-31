import { stateStore } from "../state";
import { headerState } from "./ReviewHeader.fixture";

export function setConeScene(scene: "a" | "b" | "d") {
  const state = headerState(scene === "a" ? "b" : scene);
  if (scene === "a") {
    const change = state.changes["change-1"];
    if (!change?.proposal || !state.graph) throw new Error("Missing fixture");
    change.proposal.impact = {
      ...change.proposal.impact,
      entries: [],
      omitted_by_depth: 0,
      omitted_by_limit: 0,
    };
    const seed = change.proposal.impact.seeds[0] ?? "";
    const caller = state.graph.nodes.find((node) => node.kind === "function" && node.id !== seed);
    if (!caller) throw new Error("Missing possible caller");
    state.graph.edges = [
      {
        id: "possible-fixture",
        src: caller.id,
        dst: seed,
        kind: "calls",
        confidence: "possible",
        reason: "unknown_receiver",
      },
    ];
    change.graph.edges_added = [];
    change.proposal.graph.edges_added = [];
  }
  stateStore.setState({ state });
}

declare global {
  interface Window {
    coneScene: typeof setConeScene;
  }
}
