import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { FilesProvider } from "../FilesContext";
import { WhereTheyWork } from "./WhereTheyWork";
import { event } from "../chatAgents.fixture";
import { AgentsRail } from "./Agents";
import { ChatAgents } from "./ChatAgents";
import { ChatLeadRow } from "./ChatLeadRow";
import { railState } from "../teamRail.fixture";
import { chatTeamGroups } from "./chatTeamGroups";
import { ChatAgentsFrame, ChatEmpty } from "./ChatShell";
import { required, fold, scenario } from "../chatAgents.fixture";
import { createInitialState } from "../../state";

const disclosure = vi.hoisted(() => ({ closed: new Set<string>() }));
vi.mock("react", async (original) => {
  const react = await original<typeof import("react")>();
  return {
    ...react,
    useState: <T,>(initial: T) =>
      disclosure.closed.size
        ? [{ ...initial, closed: disclosure.closed }, vi.fn()]
        : react.useState(initial),
  };
});

it.each([
  ["empty", "0 working"],
  ["working", "2 working"],
  ["proposed", "1 working · 1 waiting for you"],
  ["review", "1 working · 1 waiting for you"],
  ["merged", "0 working · 1 waiting for you"],
] as const)("renders the activity header for %s without capture flags", (name, count) => {
  const html = renderToStaticMarkup(<ChatAgentsFrame state={fold(scenario(name))} />);
  expect(html).toContain(
    `<header><h2>Agents</h2><span>${count}</span><div class="chat-start-actions">`,
  );
  const header = html.split("</header>")[0] ?? "";
  expect(header.match(/<button/g)).toHaveLength(2);
  expect(header).toContain("New task</button>");
  expect(header).toContain("New lead</button></div>");
});
it("omits the numeric header before metadata and counts agents, not slots", () => {
  const initial = renderToStaticMarkup(<ChatAgentsFrame state={createInitialState()} />);
  expect(initial).toContain('<header><h2>Agents</h2><div class="chat-start-actions">');
  expect(initial).not.toMatch(/\d+ working/);
  const state = fold(scenario("working"));
  if (!state.run) throw new Error("Missing fixture run");
  const html = renderToStaticMarkup(
    <ChatAgentsFrame
      state={{ ...state, run: { ...state.run, slots: state.run.slots.slice(0, 1) } }}
    />,
  );
  expect(html).toContain("<span>2 working</span>");
});

it("exposes full identities, selected state and facts in a native button list", () => {
  const onOpenConversation = vi.fn();
  const html = renderToStaticMarkup(
    <ChatAgents
      state={fold(scenario())}
      selectedAgentId="agent-a"
      onOpenConversation={onOpenConversation}
    />,
  );
  expect(html).toContain('<ul class="chat-agent-list" aria-label="Agents">');
  expect(html.match(/<button/g)).toHaveLength(2);
  expect(html).toContain('aria-pressed="true"');
  expect(html).toContain('aria-pressed="false"');
  expect(html).toContain("Agent A: compute_price in shop/pricing.py accepts a negative");
  expect(html).toContain("US$0.03");
  expect(html).not.toContain("Review change");
  expect(onOpenConversation).not.toHaveBeenCalled();
});
it("shows truthful noninteractive replay rows if navigation is absent", () => {
  const html = renderToStaticMarkup(<ChatAgents state={fold(scenario("merged"))} />);
  expect(html).not.toMatch(/<button|tabindex|aria-pressed/);
  expect(html).toContain("Merged by your rules");
  expect(html).not.toContain("Free");
});

it("retains full collision labels, lead targets and the New lead draft", () => {
  const state = fold(scenario("working"));
  const html = renderToStaticMarkup(
    <ChatAgents state={state} leadDraft onOpenConversation={vi.fn()} />,
  );
  expect(html).toContain('data-agent-origin="agent-a"');
  expect(html).toContain('data-agent-origin="agent-b"');
  expect(html).toContain('aria-label="New lead draft"');
});

it("exposes phone work chips with explicit file targets and graph-independent observed ranges", () => {
  const state = fold([
    ...scenario("working"),
    event("agent.focus", {
      agent_id: "agent-a",
      mode: "edit",
      node_ids: [],
      ranges: [{ path: "long/observed/path.py", start_line: 12, end_line: 24 }],
    }),
  ]);
  const openFile = vi.fn();
  const html = renderToStaticMarkup(
    <FilesProvider state={state} openFile={openFile}>
      <WhereTheyWork />
    </FilesProvider>,
  );
  expect(html).toContain('aria-label="Open long/observed/path.py · Agent A · Editing"');
  expect(html).toContain('aria-description="lines 12 to 24"');
  expect(html).toContain('class="work-chip"');
  expect(html).toContain('class="work-path"');
  expect(openFile).not.toHaveBeenCalled();
});

it("keeps eleven folded origins and lamps inside nonshrinking tiles", () => {
  const state = fold([
    ...scenario("working"),
    ...Array.from({ length: 9 }, (_, i) =>
      event("agent.status", {
        ...required(Object.values(fold(scenario("working")).agents)[0]),
        agent_id: `extra-${String(i)}`,
        change_id: `extra-${String(i)}`,
      }),
    ),
  ]);
  const html = renderToStaticMarkup(<AgentsRail state={state} />);
  expect(html.match(/data-agent-origin=/g)).toHaveLength(11);
  expect(html.match(/agents-rail-tile/g)).toHaveLength(11);
  expect(html.match(/agents-rail-lamp absolute top-0 right-0/g)).toHaveLength(11);
  expect(html).not.toContain("-top-1");
});

it.each([0, 1, 2])("retains grouped counts accessibly with %i needs-you facts", (needsYou) => {
  const state = railState("chat-busy");
  const standaloneLead = required(required(chatTeamGroups(state)[0]).lead);
  if (needsYou) {
    state.agents.a1 = { ...required(state.agents.a1), state: "ready_for_review" };
  }
  if (needsYou === 2) {
    const proposedPlan = event("plan.updated", {
      plan_id: "proposed-plan",
      started_by: "lead-a",
      revision: 1,
      task: "Proposed follow-up",
      graph_version: "fixture",
      state: "proposed",
      subtasks: null,
      detail: null,
      queued_task_id: null,
    });
    if (proposedPlan.type !== "plan.updated") throw new Error("Missing proposed plan fixture");
    state.decisionEvents.push(proposedPlan);
  }
  const group = required(chatTeamGroups(state)[0]);
  expect(group.summary).toContain(needsYou ? "1 working" : "2 working");
  if (needsYou) expect(group.summary).toContain(needsYou === 1 ? "1 needs you" : "2 need you");
  const grouped = renderToStaticMarkup(<ChatAgents state={state} />);
  expect(grouped).toContain(`<h3>${group.summary}</h3>`);
  const row = required(grouped.match(/<div class="chat-agent-row chat-lead-row"[^>]*>/)?.[0]);
  expect(required(row.match(/title="([^"]*)"/)?.[1])).toContain(group.summary);
  expect(required(row.match(/aria-description="([^"]*)"/)?.[1])).toContain(group.summary);
  expect(grouped).not.toContain('class="chat-lead-team"');
  const standalone = renderToStaticMarkup(<ChatLeadRow row={standaloneLead} />);
  expect(standalone).toContain(
    `<span class="chat-lead-team">${standaloneLead.teamLine ?? ""}</span>`,
  );
  expect(renderToStaticMarkup(<ChatAgents state={railState("chat-leadless")} />)).toContain(
    "Plan B · 2 agents · 2 working",
  );
});

it("retains scripted member labels when a team is folded", () => {
  const state = railState("chat-collapsed-selected");
  if (!state.run) throw new Error("Missing run");
  state.run.models = state.run.models.map((profile) => ({ ...profile, provider: "fake" }));
  const group = required(
    chatTeamGroups(state).find((group) => group.members.some((row) => row.id === "a1")),
  );
  disclosure.closed.add(group.id);
  try {
    const html = renderToStaticMarkup(<ChatAgents state={state} selectedAgentId="a1" />);
    expect(html).toMatch(/class="chat-team-members"[^>]*hidden=""/);
    expect(html).toContain('aria-expanded="false"');
    expect(html).toMatch(/hidden=""[\s\S]*Scripted demo agents/);
    expect(html).toMatch(
      /class="chat-agent-model">[^<]*<\/span><span class="scripted-agent-label">Scripted demo agents<\/span>/,
    );
    expect(html).toContain('data-agent-origin="a1" data-selected="true"');
    expect(html).toContain("A·1");
  } finally {
    disclosure.closed.clear();
  }
});
it("keeps the start actions in place on a past event and says why they are off", () => {
  const live = renderToStaticMarkup(<ChatAgentsFrame state={fold(scenario("empty"))} />);
  expect(live).not.toContain("past event");
  expect(live).not.toContain("aria-describedby");
  const html = renderToStaticMarkup(<ChatAgentsFrame state={fold(scenario("empty"))} readOnly />);
  const header = html.split("</header>")[0] ?? "";
  const id =
    /<p id="([^"]+)" class="chat-start-reason">Not available while viewing a past event.<\/p>/.exec(
      header,
    )?.[1];
  expect(id).toBeTruthy();
  const buttons = header.match(/<button[^>]*>/g) ?? [];
  expect(buttons).toHaveLength(2);
  for (const button of buttons) {
    expect(button).toContain('aria-disabled="true"');
    expect(button).toContain(`aria-describedby="${id ?? ""}"`);
    expect(button).toContain('title="Not available while viewing a past event."');
    expect(button).not.toContain(" disabled");
  }
  const empty = renderToStaticMarkup(<ChatEmpty state={fold(scenario("empty"))} readOnly />);
  const reason = /<p id="([^"]+)" class="chat-start-reason">/.exec(empty)?.[1];
  expect(reason).toBeTruthy();
  expect(empty).toMatch(
    new RegExp(`<button[^>]*aria-disabled="true"[^>]*aria-describedby="${reason ?? ""}"`),
  );
});
