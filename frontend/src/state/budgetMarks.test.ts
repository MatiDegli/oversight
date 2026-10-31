import { expect, it } from "vitest";
import { progressEvents } from "../agents";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";

it("retains one immutable mark per rise, ignores ok and falls, and clears on run.started", () => {
  let state = progressEvents().slice(0, 18).reduce(applyEvent, createInitialState());
  const tick = progressEvents().find((e) => e.type === "usage.tick");
  const run = progressEvents()[0];
  if (tick?.type !== "usage.tick" || run?.type !== "run.started")
    throw new Error("Missing fixture");
  for (const brake of [
    "ok",
    "warn_50",
    "warn_50",
    "ok",
    "warn_80",
    "warn_50",
    "blocked",
    "warn_80",
  ] as const) {
    const before = state.budgetMarks;
    const snapshot = [...before];
    state = applyEvent(state, {
      ...tick,
      seq: state.lastSeq + 1,
      payload: { ...tick.payload, brake },
    });
    expect(before).toEqual(snapshot);
  }
  expect(state.budgetMarks.map((mark) => mark.brake)).toEqual(["warn_50", "warn_80", "blocked"]);
  expect(state.budgetMarks.map((mark) => mark.seq)).toEqual([20, 23, 25]);
  expect(state.budgetMarks[0]).toMatchObject({
    ts: tick.ts,
    spent_usd: tick.payload.spent_usd,
    budget_usd: 150,
  });
  expect(
    applyEvent(state, { ...run, seq: 1, payload: { ...run.payload, run_id: "next" } }).budgetMarks,
  ).toEqual([]);
});
