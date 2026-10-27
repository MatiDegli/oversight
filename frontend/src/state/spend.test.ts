import type { ServerEvent, UsageTick } from "@contracts";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState, createStateStore, selectSpend } from "./index";
import { appendEvent, through } from "./panels.fixture";

const tick: ServerEvent & { type: "usage.tick" } = {
  seq: 2,
  ts: "2026-10-27T14:03:13.000Z",
  type: "usage.tick",
  payload: {
    agent_id: "agent-1",
    model: "muse-spark",
    input_tokens: 100,
    output_tokens: 10,
    cached_tokens: null,
    cost_usd: 0.4,
    spent_usd: 12.4,
    budget_usd: 150,
    brake: "ok",
  },
};

describe("spend events and selection", () => {
  it.each<UsageTick["brake"]>(["ok", "warn_50", "warn_80", "blocked"])(
    "preserves the server's %s brake and cumulative balance without mutating state",
    (brake) => {
      const before = through(1);
      Object.freeze(before);
      const event = { ...tick, payload: { ...tick.payload, brake } };
      Object.freeze(event.payload);
      Object.freeze(event);
      const state = applyEvent(before, event);
      expect(state.usage).toEqual(event.payload);
      expect(before.usage).toBeNull();
      expect(selectSpend(state)).toEqual({ spentUsd: 12.4, budgetUsd: 150, brake });
      expect(state.lastSeq).toBe(2);
    },
  );

  it("keeps null cost unknown, preserving the ledger balance and blocked state", () => {
    const state = applyEvent(through(1), {
      ...tick,
      payload: { ...tick.payload, cost_usd: null, brake: "blocked" },
    });
    expect(state.usage?.cost_usd).toBeNull();
    expect(state.usage?.spent_usd).toBe(12.4);
    expect(selectSpend(state)).toEqual({ spentUsd: null, budgetUsd: 150, brake: "blocked" });
  });

  it("uses the latest tick across agents, including a known zero cost", () => {
    const store = createStateStore(through(1));
    store.getState().applyEvents([
      tick,
      {
        ...tick,
        seq: 3,
        payload: { ...tick.payload, agent_id: "agent-2", cost_usd: 0, spent_usd: 13.5 },
      },
    ]);
    expect(selectSpend(store.getState().state)?.spentUsd).toBe(13.5);
  });

  it.each([
    [0.4, null, null],
    [null, 0.4, 13.5],
  ])("replaces cost %s with %s from the next tick", (firstCost, nextCost, expected) => {
    const store = createStateStore(through(1));
    store.getState().applyEvents([{ ...tick, payload: { ...tick.payload, cost_usd: firstCost } }]);
    expect(selectSpend(store.getState().state)?.spentUsd).toBe(firstCost === null ? null : 12.4);
    store.getState().applyEvents([
      {
        ...tick,
        seq: 3,
        payload: { ...tick.payload, cost_usd: nextCost, spent_usd: 13.5 },
      },
    ]);
    expect(selectSpend(store.getState().state)?.spentUsd).toBe(expected);
  });

  it("leaves usage unchanged on a missing sequence", () => {
    const state = applyEvent(through(1), tick);
    const gap = applyEvent(state, { ...tick, seq: 4 });
    expect(gap.gap).toBe(true);
    expect(gap.usage).toBe(state.usage);
  });

  it.each([
    [0, "ok"],
    [75, "warn_50"],
    [120, "warn_80"],
    [135, "warn_80"],
    [135.01, "blocked"],
  ] as const)(
    "reads the starting balance %s and thresholds before the first tick",
    (spent, brake) => {
      const state = through(1);
      if (!state.run) throw new Error("Missing run fixture");
      state.run = {
        ...state.run,
        budget: { budget_usd: 150, spent_usd: spent, warn_at: [0.5, 0.8], block_at: 0.9 },
      };
      expect(selectSpend(state)).toEqual({ spentUsd: spent, budgetUsd: 150, brake });
    },
  );

  it("hides without a run or budget, even if a fake tick has arrived", () => {
    expect(selectSpend(createInitialState())).toBeNull();
    const state = through(1);
    if (!state.run) throw new Error("Missing run fixture");
    state.run = { ...state.run, budget: { ...state.run.budget, budget_usd: 0 } };
    expect(selectSpend(state)).toBeNull();
    expect(selectSpend(applyEvent(state, tick))).toBeNull();
  });

  it("resets unknown usage on a new run and returns to its starting balance", () => {
    const state = applyEvent(through(1), {
      ...tick,
      payload: { ...tick.payload, cost_usd: null, brake: "blocked" },
    });
    if (!state.run) throw new Error("Missing run fixture");
    const fresh = applyEvent(state, {
      seq: 1,
      ts: tick.ts,
      type: "run.started",
      payload: { ...state.run, run_id: "next-run" },
    });
    expect(fresh.usage).toBeNull();
    expect(selectSpend(fresh)?.spentUsd).toBe(fresh.run?.budget.spent_usd);
  });

  it("honors a fake profile's ok brake even with a nearly spent configured budget", () => {
    const state = through(1);
    if (!state.run) throw new Error("Missing run fixture");
    state.run = {
      ...state.run,
      models: [{ id: "fake", label: "Fake", provider: "fake", model: "fake", reasoning: null }],
      budget: { ...state.run.budget, spent_usd: 149, budget_usd: 150 },
    };
    expect(selectSpend(state)?.brake).toBe("ok");
    expect(selectSpend(appendEvent(state, tick))?.brake).toBe("ok");
  });

  describe.each([
    { providers: ["baseten"] },
    { providers: ["fake", "baseten"] },
    { providers: ["baseten", "meta"] },
  ] as const)("pre-tick providers $providers", ({ providers }) => {
    it.each([
      [75, "warn_50"],
      [120, "warn_80"],
      [149, "blocked"],
    ] as const)("only infers a Meta brake at starting balance %s", (spent, metaBrake) => {
      const state = through(1);
      if (!state.run) throw new Error("Missing run fixture");
      state.run = {
        ...state.run,
        models: providers.map((provider) => ({
          id: provider,
          label: provider,
          provider,
          model: provider,
          reasoning: null,
        })),
        budget: { budget_usd: 150, spent_usd: spent, warn_at: [0.5, 0.8], block_at: 0.9 },
      };
      expect(selectSpend(state)?.brake).toBe(
        providers.some((p) => p === "meta") ? metaBrake : "ok",
      );
    });
  });
});
