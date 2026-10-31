import { existsSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import * as selectors from "../state";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import kinds from "../../../fixtures/events/handwritten-inbox-kinds.jsonl?raw";
import leadPlans from "../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { DecisionInbox } from "./DecisionInbox";

const state = log.trim().split("\n").map(parseEvent).reduce(applyEvent, createInitialState());

it("keeps the inbox browser coverage inside the review module's allowed paths", () => {
  expect(existsSync(new URL("./inbox.browser.test.ts", import.meta.url))).toBe(true);
  expect(existsSync(new URL("../app/inbox.browser.test.ts", import.meta.url))).toBe(false);
});

it("projects decision events once for all inbox rows and their change cards", () => {
  const projection = vi.spyOn(selectors, "selectDecisionInbox");
  try {
    const html = renderToStaticMarkup(<DecisionInbox state={state} />);
    expect(html).toContain("2 choices need you");
    expect(html).toContain("Needs you because a check was unavailable");
    expect(projection).toHaveBeenCalledTimes(1);
  } finally {
    projection.mockRestore();
  }
});

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

const leadEvents = leadPlans.trim().split("\n").map(parseEvent);
const leadState = leadEvents.slice(0, 8).reduce(applyEvent, createInitialState());
const kindsState = kinds
  .trim()
  .split("\n")
  .map(parseEvent)
  .reduce(applyEvent, createInitialState());

function rowContaining(html: string, text: string): string {
  const row = html
    .match(/<article class="inbox-row">.*?<\/article>/gs)
    ?.find((row) => row.includes(text));
  if (!row) throw new Error(`Missing inbox row: ${text}`);
  return row;
}

it("renders a single non-change reason once, without an empty paragraph or list", () => {
  const reason = "Revert conflicts with main";
  const row = rowContaining(renderToStaticMarkup(<DecisionInbox state={kindsState} />), reason);
  expect(row.split(reason)).toHaveLength(2);
  expect(row).toContain(`<h3>${reason}</h3>`);
  expect(row).not.toContain("<p>");
  expect(row).not.toContain('class="decision-reasons"');
});

it.each(["plan-1", "plan-2", "plan-3"])("renders %s's task and detail once", (planId) => {
  const event = leadEvents.find(
    (event) => event.type === "plan.updated" && event.payload.plan_id === planId,
  );
  if (event?.type !== "plan.updated" || !event.payload.detail)
    throw new Error("Missing plan detail");
  const { task, detail } = event.payload;
  const row = rowContaining(renderToStaticMarkup(<DecisionInbox state={leadState} />), task);
  expect(row).toContain(`<h3>${task}</h3>`);
  expect(row).toContain(`<p>${detail}</p>`);
  expect(row.split(task)).toHaveLength(2);
  expect(row.split(detail)).toHaveLength(2);
  expect(row).not.toContain('class="decision-reasons"');
});

it("keeps the fallback for a plan without detail and ignores an older plan update", () => {
  const event = leadEvents[7];
  if (event?.type !== "plan.updated") throw new Error("Missing plan");
  const updated = applyEvent(leadState, {
    ...event,
    seq: 9,
    payload: { ...event.payload, revision: 2, detail: null },
  });
  const stale = applyEvent(updated, { ...event, seq: 10 });
  const row = rowContaining(
    renderToStaticMarkup(<DecisionInbox state={stale} />),
    event.payload.task,
  );
  expect(row).toContain(`<h3>${event.payload.task}</h3>`);
  expect(row).toContain("<p>Lead plan awaits approval</p>");
  expect(row.split("Lead plan awaits approval")).toHaveLength(2);
});

it("omits a plan paragraph when its detail equals its heading", () => {
  const event = leadEvents[7];
  if (event?.type !== "plan.updated") throw new Error("Missing plan");
  const next = applyEvent(leadState, {
    ...event,
    seq: 9,
    payload: { ...event.payload, revision: 2, detail: event.payload.task },
  });
  const row = rowContaining(
    renderToStaticMarkup(<DecisionInbox state={next} />),
    event.payload.task,
  );
  expect(row.split(event.payload.task)).toHaveLength(2);
  expect(row).not.toContain("<p>");
  expect(row).not.toContain('class="decision-reasons"');
});

it("lists a second distinct reason once, excluding the heading and paragraph", () => {
  const entry = selectors
    .selectDecisionInbox(kindsState)
    .find((entry) => entry.decisions.some((d) => d.kind === "revert_needs_you"));
  const decision = entry?.decisions[0];
  if (!entry || !decision) throw new Error("Missing non-change entry");
  const reason = "Check the new main commit before trying again.";
  const additional = { ...decision, id: "second-reason", reason };
  const projection = vi
    .spyOn(selectors, "selectDecisionInbox")
    .mockReturnValue([
      { ...entry, decisions: [decision, additional, { ...additional, id: "repeated-reason" }] },
    ]);
  try {
    const row = rowContaining(renderToStaticMarkup(<DecisionInbox state={kindsState} />), reason);
    expect(row).toContain(`<ul class="decision-reasons"><li>${reason}</li></ul>`);
    expect(row.split(decision.reason)).toHaveLength(2);
    expect(row.split(reason)).toHaveLength(2);
  } finally {
    projection.mockRestore();
  }
});
