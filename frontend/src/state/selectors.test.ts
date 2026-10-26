import type { ChangeUpdated, ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";

import { appendEvent, panelEvents, through } from "./panels.fixture";
import { selectAgentCard, selectLiveDiff } from "./selectors";
import { createInitialState } from "./types";

describe("panel selectors over the level 1 fixture", () => {
  it("selects running, every finished step and the ready pill without mutating state", () => {
    expect(selectAgentCard(createInitialState(), "agent-1")).toBeUndefined();
    expect(selectAgentCard(through(4), "agent-1")?.agent.state).toBe("working");
    expect(selectAgentCard(through(4), "agent-1")?.steps).toEqual([]);
    for (const event of panelEvents) {
      if (event.type !== "agent.step") continue;
      const state = through(event.seq);
      const before = structuredClone(state);
      Object.freeze(state.steps[event.payload.agent_id]);
      const card = selectAgentCard(state, event.payload.agent_id);
      expect(card?.steps[0]).toEqual(event.payload);
      expect(card?.step).toBe(event.payload.step);
      expect(card?.steps.map((entry) => entry.step)).toEqual(
        Array.from({ length: event.payload.step }, (_, index) => event.payload.step - index),
      );
      expect(state).toEqual(before);
    }
    expect(selectAgentCard(through(19), "agent-1")?.readyForReview).toBe(false);
    expect(selectAgentCard(through(20), "agent-1")?.readyForReview).toBe(true);
    expect(selectAgentCard(through(23), "agent-1")?.readyForReview).toBe(false);
  });

  it("selects full contents by change and selected file as subsequent edits arrive", () => {
    const event = panelEvents.find((event) => event.type === "change.updated");
    expect(event).toBeDefined();
    if (!event) throw new Error("Fixture needs change.updated");
    const { original, modified, path } = event.payload.file;
    expect(selectLiveDiff(through(11), "change-1", path)).toBeUndefined();
    const first = through(12);
    const firstInput = selectLiveDiff(first, "change-1", path);
    expect(firstInput).toMatchObject({ original, modified, path, author: "agent" });
    const update = (payload: ChangeUpdated): ServerEvent => ({ ...event, payload });
    const second = appendEvent(
      first,
      update({
        ...event.payload,
        file: {
          ...event.payload.file,
          path: "shop/other.py",
          original: null,
          modified: "new file",
        },
      }),
    );
    const third = appendEvent(
      second,
      update({
        ...event.payload,
        author: "user",
        file: { ...event.payload.file, modified: "latest complete file", lines_added: 3 },
      }),
    );
    expect(selectLiveDiff(third, "change-1", path)).toMatchObject({
      original,
      modified: "latest complete file",
      author: "user",
      linesAdded: 3,
    });
    expect(selectLiveDiff(third, "change-1", "shop/other.py")).toMatchObject({
      original: "",
      modified: "new file",
    });
    expect(selectLiveDiff(first, "change-1", path)).toEqual(firstInput);
    expect(selectLiveDiff(third, "missing", path)).toBeUndefined();
    expect(selectLiveDiff(third, "change-1", "missing.py")).toBeUndefined();
    const otherAgent = appendEvent(
      third,
      update({ ...event.payload, agent_id: "agent-2", change_id: "change-2" }),
    );
    expect(selectLiveDiff(otherAgent, "change-2", path)?.modified).toBe(modified);
    expect(selectLiveDiff(otherAgent, "change-1", path)?.modified).toBe("latest complete file");
    const deleted = appendEvent(
      otherAgent,
      update({
        ...event.payload,
        file: { ...event.payload.file, status: "deleted", modified: null },
      }),
    );
    expect(selectLiveDiff(deleted, "change-1", path)?.modified).toBe("");
  });
});
