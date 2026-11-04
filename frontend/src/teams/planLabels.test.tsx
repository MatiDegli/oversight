import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AgentCard, ConversationPanelHeader, selectAgentCard, selectConversation } from "../agents";
import { fold, plan, seed, status, event } from "../app/chatAgents.fixture";
import { AgentsRail } from "../app/shell/Agents";
import { ChatAgents } from "../app/shell/ChatAgents";
import { graphElements } from "../graph/elements";
import { mapModel } from "../graph/mapModel";
import { MapLayer } from "../graph/MapLayer";
import { themes } from "../theme";
import design from "../../../DESIGN.md?raw";
import decisions from "../../../DECISIONS.md?raw";

it("documents stable team-wide ordinals in both label rules and the superseding decision", () => {
  const rules = [
    design.split("**The Never Alone Rule.**")[1]?.split("\n\n")[0],
    design.split("**Agent labels.**")[1]?.split("\n\n")[0],
    decisions.split("## 2026-11-03 — Team-wide agent ordinals and read-only plan progress")[1],
  ];
  for (const rule of rules) {
    expect(rule).toContain("team-wide");
    expect(rule).toContain("first-observed");
  }
});

function assignedPlan(id: string, agentId: string) {
  const source = plan(id, "lead");
  if (source.type !== "plan.updated") throw new Error("Missing plan");
  return {
    ...source,
    payload: {
      ...source.payload,
      subtasks: [
        {
          agent_id: agentId,
          task: "Same subtask",
          zone: { node_ids: [], paths: [] },
          depends_on: [],
          state: "running" as const,
        },
      ],
    },
  };
}

it.each(["dark", "day"] as const)(
  "distinguishes two single-subtask plans of one lead on cards, rails, Conversation and map in %s",
  (theme) => {
    const nodeId = fold(seed).graph?.nodes.find((node) => node.kind === "function")?.id;
    if (!nodeId) throw new Error("Missing handwritten function");
    const state = fold([
      ...seed,
      ...["one", "two"].flatMap((id) => [
        assignedPlan(`plan-${id}`, id),
        status(id, { plan_id: `plan-${id}`, task: "Same subtask" }),
        event("agent.focus", { agent_id: id, mode: "edit", node_ids: [nodeId], ranges: [] }),
      ]),
    ]);
    const rail = renderToStaticMarkup(<AgentsRail state={state} />);
    const chat = renderToStaticMarkup(<ChatAgents state={state} />);
    const model = mapModel(graphElements(state), themes[theme]);
    const map = renderToStaticMarkup(
      <MapLayer frame={{ model, level: "map", zoom: 0.2, pan: { x: 0, y: 0 } }} />,
    );
    for (const [id, ordinal] of [
      ["one", 1],
      ["two", 2],
    ] as const) {
      const label = `A·${String(ordinal)}`;
      const card = selectAgentCard(state, id);
      const conversation = selectConversation(state, id, []);
      if (!card || !conversation) throw new Error("Missing agent");
      const cardHtml = renderToStaticMarkup(<AgentCard data={card} />);
      expect(cardHtml).toContain(`aria-label="Agent ${label}"`);
      expect(cardHtml).toMatch(new RegExp(`<h3[^>]*>Agent ${label}</h3>`));
      expect(rail).toContain(`aria-label="Agent ${label}: Working"`);
      expect(rail).not.toContain("rail-plan-caption");
      expect(chat).toContain(`>Agent ${label}</span>`);
      expect(chat).toContain(`aria-label="Agent ${label}: Same subtask.`);
      const header = renderToStaticMarkup(<ConversationPanelHeader header={conversation.header} />);
      expect(header).toMatch(new RegExp(`<h2[^>]*>Agent ${label}</h2>`));
      expect(map).toContain(`${label} editing</div>`);
    }
  },
);
