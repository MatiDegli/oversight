import type { ModelProfile } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ScriptedAgentLabel } from "./ScriptedAgentLabel";
import { ChatAgents } from "../app/shell/ChatAgents";
import { ChatLeadRow } from "../app/shell/ChatLeadRow";
import { ConversationPanelHeader } from "../agents/conversation/ConversationPanelHeader";
import { LeadHeaderView } from "../agents/conversation/LeadHeaderView";
import { selectConversation } from "../agents/conversation/selectConversation";
import { selectLeadConversation } from "../agents/conversation/selectLeadConversation";
import { chatEvents } from "../app/preview/previewChat.browser.support";
import { chatTeamGroups } from "../app/shell/chatTeamGroups";
import { applyEvent, createInitialState } from "../state";

const label = "Scripted demo agents";
const explanation = "Entered text does not determine scripted actions.";
it("renders exact visible plain text and optional header explanation", () => {
  expect(renderToStaticMarkup(<ScriptedAgentLabel />)).toBe(
    '<span class="scripted-agent-label">Scripted demo agents</span>',
  );
  const html = renderToStaticMarkup(<ScriptedAgentLabel explain />);
  expect(html).toContain(label);
  expect(html).toContain(explanation);
  expect(html).not.toMatch(/aria-hidden|<svg|title=/);
});
it.each(["fake", "meta", "mixed"])(
  "labels only scripted identities on all four surfaces in a %s run",
  (variant) => {
    const events = chatEvents()
      .map((event) => {
        if (event.type !== "run.started" || variant === "fake") return event;
        const models: ModelProfile[] = event.payload.models.map((profile) => ({
          ...profile,
          provider: "meta" as const,
        }));
        if (variant === "mixed")
          models.push({
            id: "scripted-lead",
            label: "Muse Spark",
            model: "arbitrary",
            provider: "fake",
            reasoning: null,
          });
        return { ...event, payload: { ...event.payload, models } };
      })
      .map((event) =>
        event.type === "lead.status" && variant === "mixed"
          ? { ...event, payload: { ...event.payload, model: "scripted-lead" } }
          : event,
      );
    const state = events.reduce(applyEvent, createInitialState());
    const coder = selectConversation(state, "agent-1", []);
    const lead = selectLeadConversation(state, "agent-900001");
    const row = chatTeamGroups(state).find((group) => group.lead)?.lead;
    if (!coder || !lead || !row) throw new Error("Missing conversations");
    expect(coder.header.scripted).toBe(variant === "fake");
    expect(lead.header.scripted).toBe(variant !== "meta");
    const rail = renderToStaticMarkup(<ChatAgents state={state} />);
    expect((rail.match(/class="scripted-agent-label"/g) ?? []).length).toBe(
      variant === "fake" ? 2 : variant === "mixed" ? 1 : 0,
    );
    const leadRow = renderToStaticMarkup(<ChatLeadRow row={row} scripted={lead.header.scripted} />);
    expect(leadRow.includes(label)).toBe(variant !== "meta");
    for (const [html, scripted] of [
      [renderToStaticMarkup(<ConversationPanelHeader header={coder.header} />), variant === "fake"],
      [renderToStaticMarkup(<LeadHeaderView header={lead.header} />), variant !== "meta"],
    ] as const) {
      expect(html.includes(label)).toBe(scripted);
      expect(html.includes(explanation)).toBe(scripted);
    }
  },
);
it.each(["done", "stopped", "halted"] as const)(
  "keeps the label readable in terminal %s states",
  (terminal) => {
    const state = chatEvents()
      .map((event) => {
        if (event.type === "agent.status")
          return { ...event, payload: { ...event.payload, state: terminal } };
        if (event.type === "lead.status")
          return { ...event, payload: { ...event.payload, state: terminal } };
        return event;
      })
      .reduce(applyEvent, createInitialState());
    const coder = selectConversation(state, "agent-1", []);
    const lead = selectLeadConversation(state, "agent-900001");
    if (!coder || !lead) throw new Error("Missing conversations");
    for (const header of [coder.header, lead.header]) {
      const html = renderToStaticMarkup(<ConversationPanelHeader header={header} />);
      expect(html).toContain(label);
      expect(html).toContain(explanation);
    }
    expect(
      renderToStaticMarkup(<ChatAgents state={state} />).match(/class="scripted-agent-label"/g) ??
        [],
    ).toHaveLength(2);
  },
);
