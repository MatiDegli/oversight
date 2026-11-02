// Reuse the mounted desktop/phone shell and its honest hand-written provenance label.
import { flushSync } from "react-dom";
import { stateStore } from "../state";
import { paramEvidence } from "./paramEvidence.browser.fixture";
import { findingFacts, findingState } from "./ReviewFindings.fixture";
import type { FindingState } from "./ReviewFindings.fixture";

export const reviewFindings = {
  async mount(name: FindingState) {
    await paramEvidence.mount("mixed");
    this.replace(name);
  },
  replace(name: FindingState, comparison = true) {
    const state = structuredClone(stateStore.getState().state);
    const next = findingFacts(findingState(name, comparison));
    const proposal = state.changes["change-1"]?.proposal;
    if (!proposal) throw new Error("Missing candidate");
    state.changeFacts["change-1"] = [
      { ...next, revision: proposal.revision, head_commit: proposal.head_commit },
    ];
    state.testExecutions = { ...state.testExecutions, ...findingState(name).testExecutions };
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  stress() {
    const state = structuredClone(stateStore.getState().state);
    const facts = findingFacts(state),
      shape = facts.shape?.[0];
    if (!shape) throw new Error("Missing shape");
    shape.path = "folder/".repeat(60) + "<img>.py";
    shape.message = "<script>literal text</script> " + "observation ".repeat(80);
    shape.evidence = [{ event_seq: null, git_object: "abcdef".repeat(30), path: shape.path }];
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  revise: () => {
    paramEvidence.revise();
  },
};
declare global {
  interface Window {
    reviewFindings: typeof reviewFindings;
  }
}
