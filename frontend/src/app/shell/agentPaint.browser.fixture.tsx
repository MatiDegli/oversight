import type { GraphSnapshot } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import log from "../../../../fixtures/events/handwritten-team-colors.jsonl?raw";
import { AgentCard, selectAgentCard } from "../../agents";
import { ReviewHeader } from "../../review";
import { applyEvent, createInitialState } from "../../state";
import { parseEvent } from "../../transport";
import { AgentsRail } from "./Agents";
import { ChatAgents } from "./ChatAgents";
import "./shell.css";

/** Six independent teams in reverse seat order, using validated event envelopes. */
export function showTeamPaint(graph: GraphSnapshot) {
  const source = log.trim().split("\n").map(parseEvent);
  const opening = source[0];
  const proposal = source.find((event) => event.type === "change.proposed");
  if (!opening || !proposal) throw new Error("Missing handwritten paint evidence");
  const agents = ["agent-6", "agent-5", "agent-4", "agent-3", "agent-2", "agent-1"].map((id) => {
    const event = source.find(
      (event) => event.type === "agent.status" && event.payload.agent_id === id,
    );
    if (event?.type !== "agent.status") throw new Error(`Missing ${id}`);
    return { ...event.payload, plan_id: null, started_by: null, state: "ready_for_review" };
  });
  const events = [
    opening,
    { type: "graph.snapshot", ts: opening.ts, payload: graph },
    ...agents.flatMap((agent) => [
      { type: "agent.status", ts: opening.ts, payload: agent },
      {
        ...proposal,
        payload: { ...proposal.payload, agent_id: agent.agent_id, change_id: agent.change_id },
      },
    ]),
  ];
  const state = events.reduce(
    (state, event, index) =>
      applyEvent(state, parseEvent(JSON.stringify({ ...event, seq: index + 1 }))),
    createInitialState(),
  );
  if (state.gap) throw new Error("Paint evidence has an event gap");
  const host = document.createElement("div");
  document.body.replaceChildren(host);
  flushSync(() => {
    createRoot(host).render(
      <div className="shell-frame bg-panel text-text">
        {agents.map((agent) => {
          const data = selectAgentCard(state, agent.agent_id);
          if (!data) throw new Error(`Missing card ${agent.agent_id}`);
          return (
            <section key={agent.agent_id} data-paint-agent={agent.agent_id}>
              <AgentCard data={data} onReview={() => undefined} />
              <ReviewHeader state={state} changeId={agent.change_id} />
            </section>
          );
        })}
        <AgentsRail state={state} />
        <ChatAgents state={state} />
      </div>,
    );
  });
}
