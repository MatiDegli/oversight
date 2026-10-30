import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { DecisionInbox } from "./DecisionInbox";

const state = log.trim().split("\n").map(parseEvent).reduce(applyEvent, createInitialState());

it.each(["change-2", "change-3", "change-4"])(
  "keeps the follow-main headline and omits empty answers for %s",
  (changeId) => {
    const next = structuredClone(state);
    const change = next.changes[changeId];
    if (!change?.proposal) throw new Error("Missing proposal");
    change.status = "conflict";
    next.decisionEvents.push({
      type: "error",
      seq: next.lastSeq + 1,
      ts: next.clock ?? "",
      payload: {
        source: "git",
        change_id: changeId,
        agent_id: change.agent_id,
        fatal: false,
        message: `Needs you — revision=${String(change.proposal.revision)}; head=${change.proposal.head_commit}; code=main_moved; reason=Follow main before deciding`,
      },
    });
    const html = renderToStaticMarkup(<DecisionInbox state={next} />);
    const row = html
      .match(/<article class="inbox-row">.*?<\/article>/gs)
      ?.find((row) => row.includes("Follow main before deciding"));
    expect(row).toBeDefined();
    expect(row).toContain("<h3>Follow main before deciding</h3>");
    expect(row).not.toContain("<summary>");
    expect(row).not.toContain('class="grant-choice"');
  },
);

it("keeps answer expanders for current choice and unavailable-check cards", () => {
  const html = renderToStaticMarkup(<DecisionInbox state={state} />);
  expect(html.match(/<summary>Answer this change<\/summary>/g)).toHaveLength(2);
});
