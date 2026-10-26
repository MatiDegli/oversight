import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState, selectAgentCard } from "../state";
import type { State } from "../state";
import { AgentCard } from "./AgentCard";

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());

function render(state: State) {
  const data = selectAgentCard(state, "agent-1");
  if (!data) throw new Error("Fixture needs agent-1");
  return renderToStaticMarkup(<AgentCard data={data} />);
}

describe("AgentCard over the level 1 fixture", () => {
  it("renders the running task, letter, status and every tool result", () => {
    const running = render(through(4));
    expect(running).toContain("Working");
    expect(running).toContain("Cap the combined discount at 30% in compute_price");
    expect(running).toContain('aria-label="Agent A"');
    expect(running).toContain("text-agent-a-text");
    expect(running).toContain("No steps yet.");
    for (const event of events) {
      if (event.type !== "agent.step") continue;
      const html = render(through(event.seq));
      expect(html).toContain(`Step ${String(event.payload.step)}`);
      expect(html).toContain(event.payload.tool);
      expect(html).toContain(event.payload.summary);
      expect(html).toContain(`${String(event.payload.duration_ms)} ms`);
      expect(html).toContain("Ok");
    }
  });

  it("adds Ready for review only on agent.status, and keeps steps newest first", () => {
    const working = render(through(19));
    expect(working).not.toContain("Ready for review");
    expect(working).not.toContain('role="status"');
    expect(working).not.toContain("border-agent-a");
    expect(working).toContain("bg-card");
    expect(working).toContain("border-card-border");
    expect(working).toContain("bg-raised");
    expect(working).toContain("bg-agent-a-tint text-agent-a-text");
    const ready = render(through(20));
    expect(ready).toContain('role="status"');
    expect(ready).toContain("Ready for review");
    expect(ready).toContain("border-agent-a");
    expect(ready.indexOf("Step 5")).toBeLessThan(ready.indexOf("Step 1"));
    expect(render(through(23))).not.toContain("Ready for review");
  });

  it("renders an error step and the halted reason, and identifies slot B in words and color", () => {
    let state = through(10);
    const previous = state.steps["agent-1"]?.[1];
    const agent = state.agents["agent-1"];
    if (!previous || !agent) throw new Error("Fixture needs step and agent");
    state = applyEvent(state, {
      seq: 11,
      ts: events[0]?.ts ?? "",
      type: "agent.step",
      payload: { ...previous, step: 3, result: "error", error: "File changed; read it again" },
    });
    state = applyEvent(state, {
      seq: 12,
      ts: events[0]?.ts ?? "",
      type: "agent.status",
      payload: { ...agent, slot: "B", state: "halted", detail: "Step limit reached", step: 3 },
    });
    const html = render(state);
    expect(html).toContain("Halted");
    expect(html).toContain("Step limit reached");
    expect(html).toContain("Error: File changed; read it again");
    expect(html).toContain("text-failed-text");
    expect(html).toContain('aria-label="Agent B"');
    expect(html).toContain("text-agent-b-text");
    expect(html).not.toContain("Ready for review");
  });
});
