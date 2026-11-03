import type { LeadViewEvent, ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import fixture from "../../../../fixtures/views/reference-cases.json";
import { applyEvent, createInitialState } from "../../state";
import { PreviewActionsContext } from "./PreviewActionsContext";
import { PreviewOption } from "./PreviewOption";
function setup() {
  const state = (fixture.events as ServerEvent[])
    .slice(0, 6)
    .reduce(applyEvent, createInitialState());
  const agent = state.agents["agent-1"];
  if (!agent) throw new Error("Missing agent");
  state.agents[agent.agent_id] = { ...agent, state: "ready_for_review" };
  const event: LeadViewEvent = {
    type: "lead.view",
    seq: 7,
    ts: "same",
    payload: {
      agent_id: "lead",
      through_seq: 6,
      caption: "<script>caption</script>",
      view: { kind: "preview", agent_id: agent.agent_id },
    },
  };
  state.leadViews.lead = [event];
  state.lastSeq = 7;
  return { state, event };
}
it("uses team text/label, escapes subordinate speech, and offers both capabilities", () => {
  const { state, event } = setup();
  const html = renderToStaticMarkup(
    <PreviewActionsContext
      value={{ state, readState: () => state, onPreview: () => {}, onReview: () => {} }}
    >
      <PreviewOption state={state} event={event} />
    </PreviewActionsContext>,
  );
  expect(html).toContain("Agent A");
  expect(html).toContain("--slot-text:var(--agent-a-text)");
  expect(html).not.toContain("agent-1");
  expect(html).toContain("&lt;script&gt;caption&lt;/script&gt;");
  expect(html).toContain('class="lead-preview-primary"');
  expect(html).toContain('class="lead-preview-secondary"');
  expect(html).not.toContain('disabled=""');
});
it.each([true, false])(
  "disables both buttons in replay or a missing-capability session (%s)",
  (replay) => {
    const { state, event } = setup();
    const html = renderToStaticMarkup(
      <PreviewOption state={state} event={event} replay={replay} />,
    );
    expect(html.match(/disabled=""/g)).toHaveLength(2);
    expect(html).toContain(replay ? "unavailable in replay" : "unavailable in this session");
  },
);
it("shows one reason and no actions for a stale suggestion", () => {
  const { state, event } = setup();
  state.leadViews = {};
  const html = renderToStaticMarkup(<PreviewOption state={state} event={event} />);
  expect(html).toContain("unavailable in this run");
  expect(html).not.toContain("<button");
});
