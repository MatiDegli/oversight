import { renderToStaticMarkup as html } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import log from "../../../fixtures/events/handwritten-team-colors.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { tabbedView } from "./view";
import { ChatChangeCard } from "./shell/ChatChangeCard";
import { ChatReview } from "./shell/ChatReview";

const state = log
  .trim()
  .split("\n")
  .map(parseEvent)
  .slice(0, 19)
  .reduce(applyEvent, createInitialState());
afterEach(() => vi.unstubAllGlobals());

it("uses A·3 and team orange for the Chat proposal whose runtime seat is C", () => {
  expect(state.agents["agent-3"]?.slot).toBe("C");
  const output = html(
    <ChatChangeCard state={state} changeId="change-3" seq={19} reviewing={false} />,
  );
  expect(output).toContain("Agent A·3 says");
  expect(output).toContain('class="chat-proposal" style="--slot-graphic:var(--agent-a)');
});

it.each(["agent-3", "absent"])("names the narrow Chat conversation for %s", (agentId) => {
  vi.stubGlobal("window", { matchMedia: () => ({ matches: true }) });
  const output = html(
    <ChatReview
      chat
      state={state}
      view={{ ...tabbedView, review: "change-3" }}
      agentId={agentId}
      conversation={<p>Thread</p>}
      canOpenChange={() => false}
      onNodeTap={() => {}}
      onOpenChange={() => {}}
      onClosePanel={() => {}}
    />,
  );
  expect(output).toContain(
    `>Agent ${agentId === "agent-3" ? "A·3" : "unknown"} · conversation</button>`,
  );
});
