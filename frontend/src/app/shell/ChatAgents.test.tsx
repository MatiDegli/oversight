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
  expect(html).toContain(
    `<header><h2>Agents</h2><span>${String(count)} running</span><div class="chat-start-actions">`,
  );
  const header = html.split("</header>")[0] ?? "";
  expect(header.match(/<button/g)).toHaveLength(2);
  expect(header).toContain("New task</button>");
  expect(header).toContain("New lead</button></div>");
});
it("omits the numeric header before metadata and derives the denominator from the run", () => {
  const initial = renderToStaticMarkup(<ChatAgentsFrame state={createInitialState()} />);
  expect(initial).toContain('<header><h2>Agents</h2><div class="chat-start-actions">');
  expect(initial).not.toMatch(/of \d+ running/);
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
