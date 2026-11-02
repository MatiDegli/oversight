import { expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import log from "../../../fixtures/events/handwritten-team-colors.jsonl?raw";
import { parseEvent } from "../transport";
import { applyEvent, createInitialState } from "../state";
import { AgentCard, selectAgentCard, selectConversation, ConversationPanelHeader } from "../agents";
import { ReviewHeader, loadDecisionInbox } from "../review";
import { ChatAgents } from "../app/shell/ChatAgents";
import { fileMarks } from "../app/filesTree";
import { replayMarks } from "../app/replay/model";
import { presentationFor, presentationStyle } from "./presentation";

const events = log.trim().split("\n").map(parseEvent);
const state = events.slice(0, 21).reduce(applyEvent, createInitialState());
it("uses A·2 and the lead's orange across cards, Conversation and flat Chat despite seat B", () => {
  const card = selectAgentCard(state, "agent-2"),
    conversation = selectConversation(state, "agent-2", []);
  if (!card || !conversation) throw new Error("Missing handwritten coding agent");
  const navigate = vi.fn();
  const fragments = [
    renderToStaticMarkup(<AgentCard data={card} onConversation={navigate} />),
    renderToStaticMarkup(<ConversationPanelHeader header={conversation.header} />),
    renderToStaticMarkup(<ChatAgents state={state} onOpenConversation={navigate} />),
  ];
  for (const html of fragments) {
    expect(html).toContain("A·2");
    expect(html).toContain("agent-a");
  }
  expect(card.agent.slot).toBe("B");
  expect(navigate).not.toHaveBeenCalled();
  const presence = [...fileMarks(state).values()].flat().find((m) => m.agentId === "agent-2");
  expect(presence?.presentation).toEqual(card.presentation);
  expect(presentationStyle(presence?.presentation)).toEqual(presentationStyle(card.presentation));
});
it("keeps pending author tags identical in inbox, review, card and rail", async () => {
  const { DecisionInbox } = await loadDecisionInbox();
  const card = selectAgentCard(state, "agent-3");
  if (!card) throw new Error("Missing pending change");
  for (const html of [
    renderToStaticMarkup(<DecisionInbox state={state} />),
    renderToStaticMarkup(<ReviewHeader state={state} changeId="change-3" />),
    renderToStaticMarkup(<AgentCard data={card} />),
    renderToStaticMarkup(<ChatAgents state={state} />),
  ]) {
    expect(html).toContain("A·3");
    expect(html).toContain("agent-a");
  }
});
it("keeps C alone and unknown actors explicit and neutral", () => {
  const card = selectAgentCard(state, "agent-6");
  if (!card) throw new Error("Missing lone agent");
  expect(card.presentation?.label).toBe("C");
  expect(card.presentation?.family).toBe("agent-c");
  expect(presentationFor(state, "usage-only")).toBeUndefined();
  expect(presentationStyle(undefined)).toEqual({});
});
it("future replay marks resolve their own prefixes without contaminating the current state", () => {
  const before = events.slice(0, 19).reduce(applyEvent, createInitialState());
  const identity = presentationFor(before, "agent-6");
  const marks = replayMarks(
    events,
    events.map((e) => e.seq),
  );
  expect(marks.find((m) => m.presentation?.agentId === "agent-10")?.presentation).toMatchObject({
    label: "G·1",
    family: "agent-c",
  });
  expect(presentationFor(before, "agent-6")).toBe(identity);
  expect(presentationFor(before, "agent-10")).toBeUndefined();
});
