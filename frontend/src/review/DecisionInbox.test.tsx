import { existsSync, readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import * as selectors from "../state";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import kinds from "../../../fixtures/events/handwritten-inbox-kinds.jsonl?raw";
import leadPlans from "../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { DecisionInbox } from "./DecisionInbox";
import { InboxHeading } from "./InboxHeader";
import PlanActions from "./PlanActions";

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

it("uses singular labels for 1 agent and 1 step", () => {
  const firstStep = leadEvents.slice(0, 4).reduce(applyEvent, createInitialState());
  const html = renderToStaticMarkup(<DecisionInbox state={firstStep} />);
  expect(html).toContain("1 agent took 1 step in 0 min.");
  expect(html).not.toContain("1 agents");
  expect(html).not.toContain("1 steps");
});

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

it("replaces the generic fallback with a lazy plan piece and ignores an older update", () => {
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
  expect(row).not.toContain("Lead plan awaits approval");
  expect(row).not.toContain("</h3><p>");
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
  expect(row).not.toContain("</h3><p>");
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

it("ships the inbox headline before any lazy plan and removes the obsolete hero count", () => {
  const lazy = readFileSync(new URL("./planActions.css", import.meta.url), "utf8");
  for (const inbox of [
    renderToStaticMarkup(<DecisionInbox state={leadState} />),
    renderToStaticMarkup(<InboxHeading count={3} agents={1} steps={3} minutes={0} />),
  ]) {
    expect(inbox).toContain('<h2 style="font-size:20px;font-weight:700;line-height:1.3">');
    expect(inbox).not.toContain('class="inbox-count"');
  }
  expect(lazy).not.toContain(".inbox-heading");
});

it("attributes the plan only to its author while tracking remains pending", () => {
  const event = leadEvents[7];
  if (event?.type !== "plan.updated") throw new Error("Missing plan");
  const html = renderToStaticMarkup(
    <PlanActions state={leadState} plan={event.payload} slots={null} />,
  );
  expect(html).toContain("Drafted by the lead</span>");
  expect(html).not.toContain("tracked by Oversight");
});

it("gives failed plans without detail a readable neutral explanation", () => {
  const event = leadEvents[7];
  if (event?.type !== "plan.updated") throw new Error("Missing plan");
  const html = renderToStaticMarkup(
    <PlanActions
      state={leadState}
      plan={{ ...event.payload, state: "failed", detail: null }}
      slots={null}
    />,
  );
  expect(html).toMatch(/<blockquote>.*?<\/svg>No detail recorded<\/blockquote>/s);
});

it("documents the visible plan groups using the Group type", () => {
  const design = readFileSync(new URL("../../../DESIGN.md", import.meta.url), "utf8")
    .split("### Lead plan actions — first slice built")[1]
    ?.split("### Settings tab")[0];
  expect(design).toContain("Queued, waiting for slots");
  expect(design).toContain("Ended without running");
  expect(design).toContain("Group type");
  expect(design).not.toContain("Queued plans");
});

it("keeps additional plan reasons while omitting only duplicate or generic fallback copy", () => {
  const entry = selectors
    .selectDecisionInbox(leadState)
    .find((entry) => entry.decisions[0]?.planId);
  const decision = entry?.decisions[0];
  if (!entry || !decision) throw new Error("Missing lead decision");
  const reason = "Check the stock recovery before starting.";
  const projection = vi.spyOn(selectors, "selectDecisionInbox").mockReturnValue([
    {
      ...entry,
      decisions: [...entry.decisions, { ...decision, id: "extra", reason }],
    },
  ]);
  try {
    expect(renderToStaticMarkup(<DecisionInbox state={leadState} />)).toContain(
      `<ul class="decision-reasons"><li>${reason}</li></ul>`,
    );
  } finally {
    projection.mockRestore();
  }
});

it.each(["starting", "failed"] as const)(
  "labels retained %s evidence as a plan at its first time",
  (status) => {
    const first = leadEvents[7];
    if (first?.type !== "plan.updated") throw new Error("Missing plan");
    const next = applyEvent(leadState, {
      ...first,
      seq: 9,
      ts: "2026-11-01T19:30:00Z",
      payload: { ...first.payload, revision: 2, state: status, queued_task_id: "queue-test" },
    });
    const row = rowContaining(
      renderToStaticMarkup(<DecisionInbox state={next} />),
      first.payload.task,
    );
    expect(row).toContain("Plan · <time>15:00 ART</time>");
    expect(row).not.toContain("Decision ·");
    const label = status === "starting" ? "Queued, waiting for slots" : "Ended without running";
    const html = renderToStaticMarkup(<DecisionInbox state={next} />);
    expect(html).toContain(`aria-label="${label}"`);
    expect(html).toContain(`<h3 class="group-label">${label}</h3>`);
  },
);

it.each(["queued", "blocked"] as const)(
  "uses slot-derived waiting copy only for queued evidence: %s",
  (status) => {
    const event = leadEvents[7];
    if (event?.type !== "plan.updated") throw new Error("Missing plan");
    const plan = { ...event.payload, queued_task_id: "queue-test" };
    const next = applyEvent(leadState, {
      seq: 9,
      ts: event.ts,
      type: "queue.updated",
      payload: {
        queued_task_id: "queue-test",
        revision: 1,
        state: status,
        plan_id: plan.plan_id,
        plan_revision: plan.revision,
        agent: null,
        agent_ids: [],
        detail: null,
        started_by: null,
      },
    });
    const html = renderToStaticMarkup(
      <PlanActions state={next} plan={plan} slots={{ held: 2, total: 2 }} />,
    );
    if (status === "queued") {
      expect(html).toContain("Needs 1 free slot; 2 of 2 slots are held.");
      expect(html).toMatch(
        /<p class="plan-explanation"><svg[^>]*class="lucide lucide-clock"[^>]*aria-hidden="true"[^>]*>.*?<\/svg>Needs 1 free slot/s,
      );
    } else {
      expect(html).toContain("Blocked");
      expect(html).not.toContain("Needs 1 free slot");
      expect(html).not.toContain("Waiting for slot evidence");
    }
  },
);
