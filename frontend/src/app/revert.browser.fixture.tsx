import { createRoot } from "react-dom/client";
// Reuse the approved review moment in this browser-only fixture.
// eslint-disable-next-line no-restricted-imports
import { headerState } from "../review/ReviewHeader.fixture";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import { applyEvent, createInitialState, selectDecisionInbox, stateStore } from "../state";
import { chooseReplaySource, parseEvent, replayBuild, replaySource } from "../transport";
import { App } from "./App";

const state = log
  .trim()
  .split("\n")
  .map(parseEvent)
  .filter((e) => e.seq < 20)
  .reduce(applyEvent, createInitialState());
const change = state.changes["change-1"];
const resolution = headerState("merged").changes["change-1"]?.resolution;
const agent = state.agents["agent-1"];
if (!change?.proposal || !resolution || !agent || !state.graph)
  throw new Error("Missing merged moment");
agent.state = "ready_for_review";
agent.task = change.proposal.task;
const head = "a731fd246cba0958d1247e681e8b53c9e3cda213";
change.proposal.head_commit = resolution.head_commit = head;
for (const facts of state.changeFacts["change-1"] ?? []) facts.head_commit = head;
if (resolution.assessment) resolution.assessment.head_commit = head;
state.graph.commit = "ac712fde4410527789b142c4be06895ad18cbd12";
stateStore.setState({ state });
if (replayBuild) Object.assign(replaySource, chooseReplaySource(null, log));
const harness = {
  merge(person = false) {
    const state = stateStore.getState().state;
    stateStore.getState().applyEvents([
      {
        seq: state.lastSeq + 1,
        ts: state.clock ?? "",
        type: "change.resolved",
        payload: { ...resolution, decided_by: person ? "person" : "rules" },
      },
    ]);
  },
  outcome(result: "reverted" | "needs_you") {
    const state = stateStore.getState().state;
    const target = state.changes["change-1"];
    if (!target?.resolutionEvent || !resolution.merge_commit || !state.graph)
      throw new Error("Missing Revert target");
    stateStore.getState().applyEvents([
      {
        seq: state.lastSeq + 1,
        ts: state.clock ?? "",
        type: "revert.resolved",
        payload: {
          change_id: "change-1",
          revision: 1,
          resolution_seq: target.resolutionEvent.seq,
          merge_commit: resolution.merge_commit,
          main_commit: state.graph.commit,
          decided_by: "person",
          result,
          revert_commit: result === "reverted" ? "ba324d1cf1579dcd145acf23e187cd24921a52da" : null,
          reason: result === "needs_you" ? "Main moved" : null,
          paths: [],
          tests: result === "reverted" ? (change.proposal?.tests ?? null) : null,
        },
      },
    ]);
  },
  inboxCount: () => selectDecisionInbox(stateStore.getState().state).length,
};
Object.assign(window, { revertHarness: harness, appStore: stateStore });
const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(<App />);
