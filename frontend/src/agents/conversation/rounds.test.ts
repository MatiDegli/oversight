import { expect, it } from "vitest";
import scripted from "../../../../fixtures/events/scripted-level2.jsonl?raw";
import braked from "../../../../fixtures/events/handwritten-two-slots-braked.jsonl?raw";
import { applyEvent, createInitialState } from "../../state";
import { parseEvent } from "../../transport";
import { selectRounds } from "./rounds";
import { selectConversation } from "./selectConversation";
import { progressEvents, progressState } from "./progressFixture";

const events = scripted.trim().split("\n").map(parseEvent);
const state = events.reduce(applyEvent, createInitialState());
const duration = (start: number, end: number) =>
  Date.parse(events[end - 1]?.ts ?? "") - Date.parse(events[start - 1]?.ts ?? "");
it("ends only opened rounds, keeps revisions and excludes the wait for the maintainer", () => {
  const rounds = selectRounds(state, "agent-2");
  expect(rounds).toHaveLength(2);
  expect(rounds.map((r) => [r.steps.length, r.outcome, r.elapsedMs])).toEqual([
    [8, "proposed, then you asked for changes", duration(5, 50)],
    [6, "proposed", duration(56, 73)],
  ]);
  expect(rounds[1]?.end?.seq).toBe(73); // Proposal 80 never opens a round.
  expect(selectRounds(state, "agent-1")[1]).toMatchObject({
    outcome: "stopped",
    end: { seq: 93 },
  });
  expect(selectRounds(state, "agent-1")[1]?.steps).toHaveLength(2);
});
it("subtracts closed and open pauses, then stops time at the first end", () => {
  let next = progressState("working");
  const agent = next.agents["agent-1"];
  if (!agent) throw new Error("Missing agent");
  const status = (time: string, state: typeof agent.state) => {
    next = applyEvent(next, {
      seq: next.lastSeq + 1,
      ts: `2026-10-31T${time}Z`,
      type: "agent.status",
      payload: { ...agent, state },
    });
  };
  status("17:07:00", "paused");
  next = { ...next, clock: "2026-10-31T17:08:00Z" };
  expect(selectRounds(next, "agent-1")[1]).toMatchObject({ elapsedMs: 240_000, paused: true });
  status("17:09:00", "revising");
  status("17:10:00", "halted");
  expect(selectRounds(next, "agent-1")[1]).toMatchObject({
    elapsedMs: 300_000,
    outcome: "halted",
    paused: false,
  });
});
it("keeps budget marks in the stream, including the existing blocked fixture", () => {
  const brakedState = braked
    .trim()
    .split("\n")
    .map(parseEvent)
    .reduce(applyEvent, createInitialState());
  expect(selectConversation(brakedState, "agent-2", [])?.entries).toContainEqual({
    kind: "budget",
    seq: 4,
    mark: brakedState.budgetMarks[0],
  });
  expect(progressEvents().map((e) => e.seq)).toEqual(progressEvents().map((_, i) => i + 1));
});
