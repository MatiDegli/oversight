import type { ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";
import recorded from "../../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent, createInitialState } from "../../state";
import { sketchEvents, sketchMessages, sketchState } from "./fixture";
import { selectConversation } from "./selectConversation";

const select = (state = sketchState) => selectConversation(state, "agent-1", sketchMessages);
describe("selectConversation", () => {
  it("orders every kind by sequence and uses the first task timestamp", () => {
    const data = select();
    expect(sketchState.gap).toBe(false);
    expect(data?.entries.map((entry) => entry.seq)).toEqual([
      2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 14, 15, 16,
    ]);
    expect(data?.entries.map((entry) => entry.kind)).toEqual([
      "task",
      "step",
      "step",
      "step",
      "step",
      "step",
      "step",
      "proposed",
      "cross-review",
      "resolution",
      "message",
      "step",
      "step",
    ]);
    expect(data?.entries[0]).toMatchObject({
      ts: "2026-10-27T16:58:00.000Z",
      text: sketchState.agents["agent-1"]?.task,
    });
    expect(data?.entries[1]).toMatchObject({ ts: "2026-10-27T16:59:00.000Z" });
    expect(data?.entries.find((entry) => entry.kind === "cross-review")).toMatchObject({
      label: "Muse Spark Open",
      findingCount: 1,
      blockingCount: 0,
      complete: true,
    });
  });
  it("excludes another agent and its change, and handles a missing agent", () => {
    const foreign = sketchEvents.slice(1).map(
      (event) =>
        ({
          ...event,
          seq: event.seq + 16,
          payload: { ...event.payload, agent_id: "agent-2", change_id: "change-2" },
        }) as ServerEvent,
    );
    const state = foreign.reduce(applyEvent, sketchState);
    expect(state.gap).toBe(false);
    expect(select(state)).toEqual(select());
    expect(selectConversation(state, "missing", [])).toBeUndefined();
  });
  it("counts each requested revision and retains the round each resolution opened", () => {
    const resolution = sketchEvents.find((event) => event.type === "change.resolved");
    if (!resolution) throw new Error("Missing resolution");
    const state = applyEvent(sketchState, { ...resolution, seq: 18 });
    expect(select()?.header.round).toBe(2);
    expect(select(state)?.header.round).toBe(3);
    expect(
      select(state)
        ?.entries.filter((entry) => entry.kind === "resolution")
        .map((entry) => entry.round),
    ).toEqual([2, 3]);
  });
  it("keeps cost unknown distinct from no usage yet and projects header fields", () => {
    const beforeUsage = sketchEvents.slice(0, 12).reduce(applyEvent, createInitialState());
    expect(select(beforeUsage)?.header.cost).toBeUndefined();
    expect(select({ ...sketchState, agentCosts: { "agent-1": null } })?.header.cost).toBeNull();
    expect(select()?.header).toMatchObject({
      slot: "A",
      state: "revising",
      modelLabel: "Muse Spark",
      reasoning: "low",
      step: 8,
      maxSteps: 60,
      cost: 0.65,
      round: 2,
      zonePaths: ["pricing.py"],
    });
    const a = sketchState.agents["agent-1"];
    if (!a) throw new Error("Missing agent");
    expect(
      select({ ...sketchState, agents: { "agent-1": { ...a, zone: null } } })?.header.zonePaths,
    ).toEqual([]);
    expect(
      select({
        ...sketchState,
        agents: { "agent-1": { ...a, zone: { node_ids: [], paths: ["a.py", "b.py"] } } },
      })?.header.zonePaths,
    ).toEqual(["a.py", "b.py"]);
  });
  it("retains withheld message text without synthesizing a message", () => {
    expect(
      selectConversation(sketchState, "agent-1", [
        { seq: 14, ts: "2026-10-27T17:02:02Z", text: null },
      ])?.entries.find((entry) => entry.kind === "message"),
    ).toMatchObject({ text: null });
    expect(
      selectConversation(sketchState, "agent-1", [])?.entries.some(
        (entry) => entry.kind === "message",
      ),
    ).toBe(false);
  });
  it("projects every recorded agent step exactly once in step order", () => {
    const events = recorded
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as ServerEvent);
    const state = events.reduce(applyEvent, createInitialState());
    const expected = events
      .filter((event) => event.type === "agent.step" && event.payload.agent_id === "agent-1")
      .map((event) => event.payload);
    const steps = selectConversation(state, "agent-1", [])
      ?.entries.filter((entry) => entry.kind === "step")
      .map((entry) => entry.step);
    expect(state.gap).toBe(false);
    expect(expected.length).toBeGreaterThan(0);
    expect(steps).toEqual(expected);
  });
});
