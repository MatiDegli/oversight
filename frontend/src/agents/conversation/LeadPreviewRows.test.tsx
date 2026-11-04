import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import fixture from "../../../../fixtures/views/reference-cases.json";
import plansSource from "../../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import type { ServerEvent } from "@contracts";
import { applyEvent, createInitialState } from "../../state";
import { parseEvent } from "../../transport";
import { selectLeadConversation } from "./selectLeadConversation";
import LeadPreviewRows from "./LeadPreviewRows";

it("forwards the plan renderer to every retained lead plan entry", () => {
  const state = plansSource
    .trim()
    .split("\n")
    .map(parseEvent)
    .reduce(applyEvent, createInitialState());
  const conversation = selectLeadConversation(state, "agent-1");
  if (!conversation) throw new Error("Missing lead conversation");
  const ids = conversation.entries.flatMap((entry) =>
    entry.kind === "lead-plan" ? [entry.planId] : [],
  );
  expect(ids).toEqual(["plan-1", "plan-2", "plan-3"]);
  const renderPlan = vi.fn((id: string) => (
    <article aria-label={`Plan ${id}`}>Plan for {id}</article>
  ));
  const html = renderToStaticMarkup(
    <LeadPreviewRows conversation={conversation} renderPlan={renderPlan} />,
  );
  expect(renderPlan.mock.calls).toEqual(ids.map((id) => [id]));
  for (const id of ids)
    expect(html).toContain(`<article aria-label="Plan ${id}">Plan for ${id}</article>`);
  expect(html.indexOf('aria-label="Plan plan-1"')).toBeLessThan(
    html.indexOf('aria-label="Plan plan-2"'),
  );
  expect(html.indexOf('aria-label="Plan plan-2"')).toBeLessThan(
    html.indexOf('aria-label="Plan plan-3"'),
  );
});

it("reuses held rows and renders the fallback once when no reply exists", () => {
  const state = (fixture.events as ServerEvent[]).reduce(applyEvent, createInitialState());
  const conversation = selectLeadConversation(state, "agent-9");
  // The reference fixture has no lead; use its real agent-independent status shape.
  const thread = conversation ?? { kind: "lead" as const, header: {} as never, entries: [] };
  const html = renderToStaticMarkup(
    <LeadPreviewRows
      conversation={{
        ...thread,
        entries: [
          {
            kind: "lead-view",
            seq: 20,
            event: {
              seq: 20,
              ts: "same",
              type: "lead.view",
              payload: {
                agent_id: "lead",
                through_seq: 1,
                caption: null,
                view: { kind: "preview", agent_id: "agent" },
              },
            },
          },
        ],
      }}
      renderView={() => <button>Preview app</button>}
    />,
  );
  expect(html.match(/Preview app/g)).toHaveLength(1);
  expect(html).toContain("Preview suggested; no matching lead reply is available.");
});
