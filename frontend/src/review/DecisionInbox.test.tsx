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

it("keeps the exact inbox header after a highest-revision lead resend", () => {
  const current = leadEvents.findLast((event) => event.type === "lead.status");
  if (current?.type !== "lead.status") throw new Error("Missing current lead");
  const before = leadEvents.reduce(applyEvent, createInitialState());
  const after = applyEvent(before, {
    ...current,
    seq: before.lastSeq + 1,
    payload: { ...current.payload, step: 99 },
  });
  const sentence = "1 agent took 4 steps in 0 min. These 1 need a person.";
  for (const value of [before, after]) {
    const html = renderToStaticMarkup(<DecisionInbox state={value} />);
    const header = html.match(/<header class="inbox-heading">.*?<\/header>/s)?.[0];
    expect(header?.match(/<p class="decision-muted">(.*?)<\/p>/s)?.[1]).toBe(sentence);
  }
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
  const projection = vi.spyOn(selectors, "selectDecisionInbox").mockReturnValue([
    {
      ...entry,
      changeId: null,
      decisions: [decision, additional, { ...additional, id: "repeated-reason" }],
    },
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

it("keeps question reasons inside the answer card without an additional list", () => {
  const entry = selectors.selectDecisionInbox(state).find((entry) => entry.changeId === "change-3");
  const question = entry?.decisions.find((decision) => decision.kind === "review_card");
  if (!entry || !question) throw new Error("Missing question entry");
  const reason = "Inspect this independent question evidence.";
  const projection = vi
    .spyOn(selectors, "selectDecisionInbox")
    .mockReturnValue([
      { ...entry, decisions: [...entry.decisions, { ...question, id: "extra-question", reason }] },
    ]);
  try {
    const row = rowContaining(
      renderToStaticMarkup(<DecisionInbox state={state} />),
      "Choices need you",
    );
    expect(row).not.toContain('class="decision-reasons"');
    expect(row).not.toContain(reason);
    expect(row).toContain('<details class="inbox-answer"><summary>Answer this change</summary>');
    const card = row.match(/<aside aria-label="Change card".*?<\/aside>/s)?.[0];
    expect(card).toBeDefined();
    expect(card?.replace(/<[^>]*>/g, "").split(question.reason)).toHaveLength(2);
    expect(card).toContain("Yes, keep it");
    projection.mockReturnValue([
      {
        ...entry,
        changeId: null,
        decisions: [question, { ...question, id: "question-only-extra", reason }],
      },
    ]);
    const questionOnly = rowContaining(
      renderToStaticMarkup(<DecisionInbox state={state} />),
      "Choices need you",
    );
    expect(questionOnly).not.toContain('class="decision-reasons"');
  } finally {
    projection.mockRestore();
  }
});

it("omits additional reasons for a change without questions and retains its answer disclosure", () => {
  const entry = selectors.selectDecisionInbox(state).find((entry) => entry.changeId === "change-4");
  const decision = entry?.decisions.find((decision) => decision.kind !== "change");
  if (!entry || !decision) throw new Error("Missing change entry");
  expect(entry.decisions.some((decision) => decision.kind === "review_card")).toBe(false);
  const reason = "Inspect this independent unavailable-check evidence.";
  const projection = vi
    .spyOn(selectors, "selectDecisionInbox")
    .mockReturnValue([
      { ...entry, decisions: [...entry.decisions, { ...decision, id: "extra-check", reason }] },
    ]);
  try {
    const row = rowContaining(
      renderToStaticMarkup(<DecisionInbox state={state} />),
      "The reserve error now names quantity.",
    );
    expect(row).not.toContain('class="decision-reasons"');
    expect(row).not.toContain(reason);
    expect(row).toContain('<details class="inbox-answer"><summary>Answer this change</summary>');
    expect(row).toContain('aria-label="Change card"');
    expect(row).toContain("Needs you because a check was unavailable");
    expect(row).toContain("Reject");
  } finally {
    projection.mockRestore();
  }
});

it("ships the inbox headline before any lazy plan and removes the obsolete hero count", () => {
  const eager = readFileSync(new URL("./inbox.css", import.meta.url), "utf8");
  const lazy = readFileSync(new URL("./planActions.css", import.meta.url), "utf8");
  for (const inbox of [
    renderToStaticMarkup(<DecisionInbox state={leadState} />),
    renderToStaticMarkup(<InboxHeading count={3} agents={1} steps={3} minutes={0} />),
  ]) {
    expect(inbox).toContain("<h2>Decisions waiting for you</h2>");
    expect(inbox).not.toContain("<style>");
    expect(inbox).not.toContain('style="');
  }
  expect(eager).toMatch(
    /\.inbox-heading h2\s*\{[^}]*font-size: 20px;[^}]*font-weight: 700;[^}]*line-height: 1\.3;/s,
  );
  expect(eager).toMatch(/\.inbox-count\s*\{\s*display: none;\s*\}/);
  expect(eager).not.toContain("font-size: 64px");
  expect(lazy).not.toContain(".inbox-heading");
});

it("records the ratified eager stylesheet and separates the next decision entry", () => {
  const decisions = readFileSync(new URL("../../../DECISIONS.md", import.meta.url), "utf8");
  const entry = decisions
    .split("## 2026-11-01 — Lead plan actions keep their eager layout within scope\n")[1]
    ?.split("## ")[0];
  expect(entry).toContain("17:25 UTC");
  expect(entry).toContain("`inbox.css`");
  expect(entry).not.toContain("exclude");
  expect(entry).not.toContain("style element");
  expect(entry).toMatch(/\n\n$/);
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

it.each([
  [1, 1, "queued", null, "Waits for the running agent to finish."],
  [1, 2, "queued", null, "Waits for one of the 2 running agents to finish."],
  [2, 2, "queued", null, "Waits for 2 of the 2 running agents to finish."],
  [1, 2, "blocked", null, null],
  [
    2,
    2,
    "queued",
    "Waiting for the spending brake to clear",
    "Waiting for the spending brake to clear",
  ],
  [1, null, "queued", null, "Waiting for slot evidence."],
  [1, 0, "queued", null, "Waiting for slot evidence."],
] as const)(
  "uses observed waiting copy for wave=%s, held=%s, state=%s, detail=%s",
  (wave, held, status, detail, words) => {
    const event = leadEvents[7];
    if (event?.type !== "plan.updated") throw new Error("Missing plan");
    const task = event.payload.subtasks?.[0];
    if (!task) throw new Error("Missing subtask");
    const plan = {
      ...event.payload,
      queued_task_id: "queue-test",
      subtasks: Array.from({ length: wave }, () => ({ ...task, depends_on: [] })),
    };
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
        detail,
        started_by: null,
      },
    });
    const html = renderToStaticMarkup(
      <PlanActions state={next} plan={plan} slots={held === null ? null : { held, total: 2 }} />,
    );
    if (status === "queued") {
      expect(html).toMatch(
        /<p class="plan-explanation"><svg[^>]*class="lucide lucide-clock"[^>]*aria-hidden="true"[^>]*>.*?<\/svg>/s,
      );
      expect(html).toContain(`</svg>${words}</p>`);
    } else {
      expect(html).toContain("Blocked");
      expect(html).not.toContain("Waits for");
      expect(html).not.toContain("Waiting for slot evidence");
      expect(html).not.toContain('<p class="plan-explanation"></p>');
    }
  },
);

it("omits the explanation spacing for a queue link without observed queue evidence", () => {
  const event = leadEvents[7];
  if (event?.type !== "plan.updated") throw new Error("Missing plan");
  const html = renderToStaticMarkup(
    <PlanActions
      state={leadState}
      plan={{ ...event.payload, queued_task_id: "unobserved" }}
      slots={null}
    />,
  );
  expect(html).toContain("Queue link recorded");
  expect(html).not.toContain('<p class="plan-explanation"></p>');
});
