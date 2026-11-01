import { describe, expect, it } from "vitest";
import log from "../../../../fixtures/events/recorded-demo.jsonl?raw";
import { parseEvent } from "../../transport";
import { applyEvent, createInitialState } from "../../state";
import { conversationTab, navigateView, surfaceTab, tabbedView, viewReducer } from "../view";
import { reconcileReplayView, replayMarks, replayTime } from "./model";

const events = log.trim().split("\n").map(parseEvent);
let elapsed = 0;
const offsets = events.map((event, index) => {
  const previous = events[index - 1];
  if (previous)
    elapsed += Math.min(2000, Math.max(0, Date.parse(event.ts) - Date.parse(previous.ts)));
  return elapsed;
});
const replayPrefix = (_events: unknown, _offsets: unknown, position: number) =>
  events
    .filter((_, i) => (offsets[i] ?? Infinity) <= position)
    .reduce(applyEvent, createInitialState());
function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Missing recording subject");
  return value;
}
const state = replayPrefix(events, offsets, Infinity);
describe("replay navigation", () => {
  it("derives future marks, identities, tests, counts and only successful merge hashes", () => {
    const marks = replayMarks(events, offsets);
    expect(marks.map((mark) => [mark.id, mark.kind, mark.letters])).toEqual([
      ["start", "start", "AB"],
      ["56", "proposal", "B"],
      ["62", "proposal", "A"],
      ["64", "merge", "A"],
      ["67", "merge", "B"],
    ]);
    const proposal = required(marks[2]);
    expect(replayTime(proposal.offset)).toBe("0:16");
    expect(proposal.proposal).toMatchObject({
      tests: { passed: 32 },
      lines_added: 28,
      lines_removed: 0,
    });
    const merge = required(events[63]);
    if (merge.type !== "change.resolved") throw new Error("Missing merge");
    expect(required(marks[3]).hash).toBe(merge.payload.merge_commit);
    expect(
      replayMarks(
        events.filter(
          (event) => event.type !== "change.resolved" || event.payload.decision !== "approved",
        ),
        offsets,
      ).some((mark) => mark.kind === "merge"),
    ).toBe(false);
  });
  it("preserves valid and neutral tabs, closes absent subjects through history, clears selection", () => {
    const agent = required(Object.keys(state.agents)[0]);
    const change = required(Object.values(state.changes)[0]);
    const path = required(Object.keys(change.files)[0]);
    let view = viewReducer(tabbedView, { type: "open", tab: surfaceTab(tabbedView, "settings") });
    view = navigateView(
      view,
      { ...view, file: { path, agentId: null, startLine: 1, endLine: 1 } },
      state,
    );
    const fileId = view.activeTab;
    view = viewReducer(view, { type: "open", tab: conversationTab(view, state, agent) });
    view = navigateView(
      view,
      { ...view, file: null, review: change.change_id, selected: "missing" },
      state,
    );
    const beforeProposal = replayPrefix(events, offsets, 8000);
    const next = reconcileReplayView(view, beforeProposal);
    expect(next.tabs?.map((tab) => tab.id)).toEqual(["settings", fileId, `conversation:${agent}`]);
    expect(next.selected).toBeNull();
    const beginning = reconcileReplayView(next, replayPrefix(events, offsets, 0));
    expect(beginning.tabs?.map((tab) => tab.id)).toEqual(["settings"]);
    expect(beginning.activeTab).toBe("settings");
  });
  it("keeps pending added nodes selected but clears discarded nodes", () => {
    const node = { ...required(state.graph?.nodes[0]), id: "pending-only" };
    const change = required(Object.values(state.changes)[0]);
    const pending = {
      ...change,
      status: "editing" as const,
      graph: { ...change.graph, nodes_added: [node] },
    };
    const view = { ...tabbedView, selected: node.id };
    expect(reconcileReplayView(view, { ...state, changes: { pending } }).selected).toBe(node.id);
    expect(
      reconcileReplayView(view, {
        ...state,
        changes: { pending: { ...pending, status: "rejected" } },
      }).selected,
    ).toBeNull();
  });
});
