import { renderToStaticMarkup as html } from "react-dom/server";
import { expect, it } from "vitest";
import { applyEvent, createInitialState } from "../state";
// eslint-disable-next-line no-restricted-imports -- Tests exercise C5 optional conversation hooks directly.
import { ConversationPanel } from "../agents/conversation/ConversationPanel";
// eslint-disable-next-line no-restricted-imports -- Reuse the retained conversation projection.
import { selectConversation } from "../agents/conversation/selectConversation";
import { ChatMergeChip, ChatMergeReceipt, resolvedReviewAction } from "./shell/ChatMerge";
import { chatMergeState, modeEvent } from "./chatMerge.browser.fixture";
import { tabbedView, viewReducer } from "./view";

it("uses current events, run fallback and unknown without inventing a mode or action", () => {
  const state = chatMergeState(false);
  expect(html(<ChatMergeChip state={state} />)).toContain("Merge mode: Ask me");
  expect(
    html(
      <ChatMergeChip state={applyEvent(state, modeEvent(state.lastSeq + 1, "merge_when_safe"))} />,
    ),
  ).toContain("Merge mode: Merge alone when safe");
  const unknown = html(<ChatMergeChip state={createInitialState()} />);
  expect(unknown).toContain("Merge mode: unknown");
  expect(unknown).not.toContain("<button");
});
function evidence() {
  const state = chatMergeState();
  const conversation = selectConversation(state, "agent-1", []);
  const entry = conversation?.entries.find((e) => e.kind === "resolution");
  if (!conversation || entry?.kind !== "resolution") throw new Error("Missing resolution");
  return { state, conversation, entry };
}
it("keeps one receipt at its resolution, with immutable mode, grant reasons, commit and time", () => {
  const { state, conversation, entry } = evidence();
  const receipt = () => html(<ChatMergeReceipt resolution={entry.resolution} ts={entry.ts} />);
  const before = receipt();
  const later = applyEvent(state, modeEvent(22, "ask_me"));
  const historical = selectConversation(later, "agent-1", [])?.entries.find(
    (e) => e.kind === "resolution",
  );
  if (historical?.kind !== "resolution") throw new Error("Missing historical entry");
  expect(html(<ChatMergeReceipt resolution={historical.resolution} ts={historical.ts} />)).toBe(
    before,
  );
  expect(before).toContain("Merge alone when safe");
  expect(before).toContain("Your grant covered");
  expect(before).toContain(entry.ts);
  expect(before).toContain(entry.resolution.merge_commit ?? "missing");
  const renderResolution = (e: typeof entry) => (
    <ChatMergeReceipt resolution={e.resolution} ts={e.ts} />
  );
  const output = html(
    <ConversationPanel
      conversation={{
        ...conversation,
        entries: [
          ...conversation.entries,
          { kind: "message", seq: 21, ts: entry.ts, text: "After resolution" },
        ],
      }}
      renderResolution={renderResolution}
    />,
  );
  expect(output.match(/aria-label="Merged by your rules"/g)).toHaveLength(1);
  expect(output.indexOf("Merged by your rules")).toBeLessThan(output.indexOf("After resolution"));
  expect(html(<ConversationPanel conversation={conversation} />)).toContain(
    "You approved the change",
  );
  const missing = html(
    <ChatMergeReceipt resolution={{ ...entry.resolution, assessment: null }} ts={entry.ts} />,
  );
  expect(missing).not.toContain("Merge mode:");
  expect(missing).not.toContain("Your grant");
  expect(missing).toContain("Its review is unavailable.");
});
it("opens only matching retained review evidence, preserving selection and tab history", () => {
  const { state, entry } = evidence();
  const action = resolvedReviewAction(state, { ...tabbedView, selected: "keep-node" }, entry);
  expect(action).toMatchObject({
    type: "open",
    tab: {
      id: "review:change-1",
      review: "change-1",
      selected: "keep-node",
      frameIds: [],
      file: null,
      diff: null,
    },
  });
  expect(action && "tab" in action && action.tab.conversation).toBeUndefined();
  if (!action) throw new Error("Missing review action");
  expect(viewReducer(tabbedView, action).review).toBe("change-1");
  expect(resolvedReviewAction({ ...state, decisionEvents: [] }, tabbedView, entry)).toBeNull();
  const change = state.changes["change-1"];
  if (!change?.proposal) throw new Error("Missing proposal");
  change.proposal.revision++;
  expect(resolvedReviewAction(state, tabbedView, entry)).toBeNull();
});
