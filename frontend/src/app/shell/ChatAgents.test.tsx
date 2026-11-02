import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { ChatAgents } from "./ChatAgents";
import { ChatAgentsFrame } from "./ChatShell";
import { fold, scenario } from "../chatAgents.fixture";
import { createInitialState } from "../../state";

it.each([
  ["empty", 0],
  ["working", 2],
  ["proposed", 2],
  ["review", 2],
  ["merged", 1],
] as const)("renders the running header for %s without capture flags", (name, count) => {
  const html = renderToStaticMarkup(<ChatAgentsFrame state={fold(scenario(name))} />);
  expect(html).toContain(`<header><h2>Agents</h2><span>${String(count)} running</span></header>`);
});
it("omits the numeric header before metadata and derives the denominator from the run", () => {
  const initial = renderToStaticMarkup(<ChatAgentsFrame state={createInitialState()} />);
  expect(initial).toContain("<header><h2>Agents</h2></header>");
  const state = fold(scenario("working"));
  if (!state.run) throw new Error("Missing fixture run");
  const html = renderToStaticMarkup(
    <ChatAgentsFrame
      state={{ ...state, run: { ...state.run, slots: state.run.slots.slice(0, 1) } }}
    />,
  );
  expect(html).toContain("<span>1 running</span>");
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
  expect(html).toContain('<ul class="chat-agent-list" aria-label="Coding agents">');
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
