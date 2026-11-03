import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import log from "../../../fixtures/events/handwritten-team-colors.jsonl?raw";
import { parseEvent } from "../transport";
import { applyEvent, createInitialState } from "../state";
import { AgentsPanel, AgentsRail } from "../app/shell/Agents";
import { selectWorkPresence } from "../app/workPresence";

const events = log.trim().split("\n").map(parseEvent);
const state = events.reduce(applyEvent, createInitialState());
const orderedIds = [
  "agent-1",
  "agent-2",
  "agent-3",
  "agent-4",
  "agent-5",
  "agent-6",
  "agent-7",
  "agent-8",
  "agent-9",
  "agent-10",
  "agent-11",
];
it.each(["cards", "tiles"])(
  "orders %s by team and plan subtask, independent of seats",
  (surface) => {
    const cards = renderToStaticMarkup(
      <AgentsPanel state={state} canOpenChange={() => false} onOpenChange={() => undefined} />,
    );
    const tiles = renderToStaticMarkup(<AgentsRail state={state} />);
    const html = surface === "cards" ? cards : tiles;
    expect([...html.matchAll(/data-agent-origin="([^"]+)"/g)].map((match) => match[1])).toEqual(
      orderedIds,
    );
  },
);
it("orders current work by team and plan subtask, independent of seats", () => {
  const working = events.slice(0, 56).reduce(applyEvent, createInitialState());
  expect(selectWorkPresence(working).current.map((group) => group.agentId)).toEqual([
    "agent-7",
    "agent-8",
    "agent-9",
    "agent-10",
    "agent-11",
  ]);
});
it("orders pending work by team and plan subtask, independent of seats", () => {
  let pending = events.slice(0, 21).reduce(applyEvent, createInitialState());
  for (const [id, slot] of [
    ["agent-3", "F"],
    ["agent-4", "A"],
  ] as const) {
    const source = events.find((e) => e.type === "agent.status" && e.payload.agent_id === id);
    const agent = pending.agents[id];
    if (source?.type !== "agent.status" || !agent) throw new Error("Missing agent");
    pending = applyEvent(pending, {
      ...source,
      seq: pending.lastSeq + 1,
      payload: { ...agent, slot },
    });
  }
  expect(selectWorkPresence(pending).pending.map((group) => group.agentId)).toEqual([
    "agent-3",
    "agent-4",
  ]);
});
