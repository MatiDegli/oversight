import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { graphElements } from "./elements";

it("keeps a live read's look at full opacity beside the cone while review suppresses it", () => {
  const events = readFileSync(
    new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
    "utf8",
  )
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
  const state = events.filter((event) => event.seq <= 14).reduce(applyEvent, createInitialState());
  const id = "shop.inventory:_check_quantity";
  state.focus["agent-1"] = { agent_id: "agent-1", mode: "read", node_ids: [id], ranges: [] };
  const live = graphElements(state, false, new Set([id]), null, true).find(
    (element) => element.data.id === id,
  );
  expect(live?.classes).toContain("focus-A-read");
  expect(live?.classes).not.toContain("dimmed");
  expect(live?.data.marks?.word).toBe("read");
  const review = graphElements(state, false, new Set([id])).find(
    (element) => element.data.id === id,
  );
  expect(review?.classes).not.toContain("focus-A-read");
});
