import type { LeadViewEvent, LeadViewKind, ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import fixture from "../../../../fixtures/views/reference-cases.json";
import { applyEvent, createInitialState } from "../../state";
import type { LeadHeader } from "./selectConversation";
import { LeadViewCard, viewCardTitle } from "./LeadViewCard";
import { selectLeadConversation } from "./selectLeadConversation";

const events = fixture.events as ServerEvent[];
const header: LeadHeader = {
  agent_id: "agent-9",
  agentId: "agent-9",
  revision: 1,
  task: "Inspect references",
  model: "fake",
  state: "idle",
  step: 0,
  detail: null,
  limits: null,
  requested_plan_id: null,
  kind: "lead",
  letter: "A",
  hue: "A",
  modelLabel: "Scripted fake model",
  reasoning: null,
  turn: 1,
  plans: 0,
  turnCalls: 0,
  cost: undefined,
};
function render(
  view: LeadViewKind,
  prefix = 12,
  caption: string | null = "Synthetic caption <b>only speech</b>",
  replay = false,
) {
  const state = events.slice(0, prefix).reduce(applyEvent, createInitialState());
  const event: LeadViewEvent = {
    seq: 100,
    ts: required(events[0]).ts,
    type: "lead.view",
    payload: { agent_id: "agent-9", through_seq: 12, caption, view },
  };
  return renderToStaticMarkup(
    <LeadViewCard
      state={state}
      event={event}
      header={header}
      replay={replay}
      actions={{ onMoment: () => {}, onFile: () => {}, onHighlight: () => {} }}
    />,
  );
}
it("selects all four known cards and renders nothing for unknown kinds", () => {
  for (const kind of ["highlight", "files", "behavior", "moment"] as const)
    expect(viewCardTitle({ kind } as LeadViewKind)).not.toBeNull();
  const unknown = { kind: "future-kind", agent_id: "agent-1" } as unknown as LeadViewKind;
  expect(viewCardTitle(unknown)).toBeNull();
  expect(render(unknown)).toBe("");
});
it("renders no card or title for a dormant preview view", () => {
  const preview: LeadViewKind = { kind: "preview", agent_id: "agent-1" };
  expect(viewCardTitle(preview)).toBeNull();
  expect(render(preview)).toBe("");
});
it("derives highlight labels/counts from nodes and retains escaped, attributed speech", () => {
  const html = render({ kind: "highlight", references: [{ kind: "agent", agent_id: "agent-1" }] });
  expect(html).toContain("Show 3 nodes on graph");
  expect(html).toContain("extra.py");
  expect(html).toContain("Lead A");
  expect(html).toContain("&lt;b&gt;only speech&lt;/b&gt;");
  expect(html).toContain("Requested through event 12; references follow current state.");
});
it("hides missing references and restores them on a later fold while keeping the caption", () => {
  const view: LeadViewKind = {
    kind: "highlight",
    references: [{ kind: "agent", agent_id: "agent-1" }],
  };
  expect(render(view, 14)).toContain("No referenced items are available at this point in the run.");
  expect(render(view, 14)).toContain("Synthetic caption");
  expect(render(view, 15)).toContain("Show 2 nodes on graph");
  expect(render(view, 14, null)).toContain("Caption omitted or withheld in this source.");
});
it("uses exact file references, including merged changes", () => {
  const html = render(
    {
      kind: "files",
      references: [
        { change_id: "change-1", path: "a.py" },
        { change_id: "change-2", path: "a.py" },
      ],
    },
    15,
  );
  expect(html).toContain("a.py");
  expect(html).toContain("approved");
  expect(html).toContain("Agent A");
  expect(html).not.toContain("change-1");
  expect(html).not.toContain("change-2");
});
it("uses only the current candidate's latest measured behavior and shared limitations", () => {
  const view: LeadViewKind = { kind: "behavior", change_id: "change-1" };
  const html = render(view, 7);
  for (const label of ["Inputs compared", "These bounded inputs do not establish equivalence."])
    expect(html).toContain(label);
  for (const prefix of [8, 9, 11]) {
    expect(render(view, prefix)).toContain("No referenced items are available");
    expect(render(view, prefix)).toContain("<h3>Measured behavior</h3>");
  }
  expect(render(view, 10)).toContain("Behavior diff");
  expect(render(view, 12)).toContain("Behavior diff");
});
it("shows live moment facts without rewind and supplies an explicit action only in replay", () => {
  const view: LeadViewKind = { kind: "moment", event_seq: 6 };
  expect(render(view)).toContain("change.proposed");
  expect(render(view)).toContain("Agent A");
  expect(render(view)).not.toContain("agent-1");
  expect(render(view)).toContain("--slot-text:var(--agent-a-text)");
  expect(render(view)).toContain("Rewind available in replay.");
  expect(render(view)).not.toContain("<button");
  expect(render(view, 12, null, true)).toContain("View event 6");
});
it("uses the referenced agent's team label and hue independently of the caption's lead", () => {
  const sequence: ServerEvent[] = [
    ...events.slice(0, 2),
    {
      seq: 0,
      ts: required(events[0]).ts,
      type: "plan.updated",
      payload: {
        plan_id: "p",
        started_by: "agent-9",
        revision: 1,
        task: "Inspect",
        graph_version: "g1",
        state: "planning",
        subtasks: null,
        detail: null,
        queued_task_id: null,
      },
    },
    ...events
      .slice(2, 12)
      .map((event) =>
        event.type === "agent.status"
          ? { ...event, payload: { ...event.payload, plan_id: "p" } }
          : event,
      ),
  ];
  const state = sequence.reduce(
    (state, event, index) => applyEvent(state, { ...event, seq: index + 1 }),
    createInitialState(),
  );
  const event: LeadViewEvent = {
    seq: 14,
    ts: required(events[0]).ts,
    type: "lead.view",
    payload: {
      agent_id: "other-lead",
      through_seq: 13,
      caption: null,
      view: { kind: "moment", event_seq: 7 },
    },
  };
  const html = renderToStaticMarkup(
    <LeadViewCard state={state} event={event} header={{ ...header, hue: "B", letter: "B" }} />,
  );
  expect(html).toContain("Agent A·1");
  expect(html).toMatch(/<dd[^>]*>.*--slot-text:var\(--agent-a-text\).*Agent A·1/);
  expect(html).not.toContain("agent-1");
});
it("puts views between status and later replies in the lead's sequence-ordered thread", () => {
  const state = events.reduce(applyEvent, createInitialState());
  const appended: ServerEvent[] = [
    {
      seq: 16,
      ts: required(events[0]).ts,
      type: "lead.status",
      payload: {
        agent_id: "agent-9",
        revision: 1,
        task: "Inspect",
        model: "fake",
        state: "idle",
        step: 0,
        detail: null,
        limits: null,
        requested_plan_id: null,
      },
    },
    {
      seq: 17,
      ts: required(events[0]).ts,
      type: "lead.view",
      payload: {
        agent_id: "agent-9",
        through_seq: 15,
        caption: null,
        view: { kind: "moment", event_seq: 6 },
      },
    },
    {
      seq: 18,
      ts: required(events[0]).ts,
      type: "lead.reply",
      payload: {
        agent_id: "agent-9",
        answers_seq: null,
        woken_by: null,
        through_seq: 17,
        text: "Later reply",
        cut: null,
      },
    },
  ];
  const thread = selectLeadConversation(appended.reduce(applyEvent, state), "agent-9");
  expect(thread?.entries.map((entry) => entry.kind)).toEqual([
    "task",
    "lead-status",
    "lead-view",
    "lead-reply",
  ]);
});
it("shows No agent for an accepted agentless milestone", () => {
  const state = events.reduce(applyEvent, createInitialState());
  const next = applyEvent(state, {
    seq: 16,
    ts: required(events[0]).ts,
    type: "error",
    payload: {
      agent_id: null,
      change_id: null,
      source: "server",
      fatal: false,
      message: "Synthetic error",
    },
  });
  const event: LeadViewEvent = {
    seq: 17,
    ts: required(events[0]).ts,
    type: "lead.view",
    payload: {
      agent_id: "agent-9",
      through_seq: 16,
      caption: null,
      view: { kind: "moment", event_seq: 16 },
    },
  };
  expect(
    renderToStaticMarkup(<LeadViewCard state={next} event={event} header={header} />),
  ).toContain("No agent");
});

it("shows measured Input/Before/After samples through the shared evidence component", () => {
  let state = events.slice(0, 7).reduce(applyEvent, createInitialState());
  const facts = required(events[6]);
  if (facts.type !== "change.facts_recorded" || !facts.payload.behavior_diff?.[0])
    throw new Error("Missing facts");
  state = applyEvent(state, {
    ...facts,
    seq: 8,
    payload: {
      ...facts.payload,
      behavior_diff: [
        {
          ...facts.payload.behavior_diff[0],
          differing_count: 1,
          rows: [{ inputs: "x=-1", before: "-1", after: "ValueError", unstable: false }],
        },
      ],
    },
  });
  const event: LeadViewEvent = {
    type: "lead.view",
    seq: 9,
    ts: required(events[0]).ts,
    payload: {
      agent_id: "agent-9",
      through_seq: 8,
      caption: null,
      view: { kind: "behavior", change_id: "change-1" },
    },
  };
  const html = renderToStaticMarkup(<LeadViewCard state={state} event={event} header={header} />);
  for (const label of ["Input", "Before", "After", "ValueError"]) expect(html).toContain(label);
});

function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Missing synthetic fixture value");
  return value;
}
