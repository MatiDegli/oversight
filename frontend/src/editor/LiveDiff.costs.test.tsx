import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

import { AgentCard, selectAgentCard } from "../agents";
import { ChangeFactsRow } from "../review";
import { applyEvent, createInitialState, costText } from "../state";

vi.mock("../state", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../state")>()),
  costText: vi.fn(() => "Shared cost wording"),
}));
vi.mock("./monaco", () => ({ monaco: { editor: {} } }));

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const state = events.filter((event) => event.seq <= 20).reduce(applyEvent, createInitialState());
const value = { ...state, agentCosts: { "agent-1": 0.001 } };

for (const live of [false, true]) {
  it(`uses the shared cost wording in ${live ? "live diff" : "review"} facts`, () => {
    vi.mocked(costText).mockClear();
    const html = renderToStaticMarkup(
      <ChangeFactsRow state={value} changeId="change-1" live={live} />,
    );
    expect(html).toContain("Shared cost wording");
    expect(costText).toHaveBeenCalledWith(0.001);
  });
}
for (const readyForReview of [false, true]) {
  it(`uses the shared cost wording in the ${readyForReview ? "ready" : "running"} agent card`, () => {
    vi.mocked(costText).mockClear();
    const card = selectAgentCard(value, "agent-1");
    if (!card) throw new Error("Missing fixture agent");
    const html = renderToStaticMarkup(<AgentCard data={{ ...card, readyForReview }} />);
    expect(html).toContain("Shared cost wording");
    expect(costText).toHaveBeenCalledWith(0.001);
  });
}
