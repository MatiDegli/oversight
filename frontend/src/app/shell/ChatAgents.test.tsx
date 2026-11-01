import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { ChatAgents } from "./ChatAgents";
import { fold, scenario } from "../chatAgents.fixture";

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
