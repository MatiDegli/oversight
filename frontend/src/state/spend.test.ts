import type { ServerEvent, UsageTick } from "@contracts";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState, createStateStore, selectSpend } from "./index";
import { appendEvent, through } from "./panels.fixture";
import { nextSlot } from "../agents";

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
  it.each(["baseten", "fake"] as const)(
    "keeps Meta's total known through a null-cost %s tick (K18)",
    (provider) => {
      const state = mixed(provider);
      const meta = appendEvent(state, {
        ...tick,
        payload: { ...tick.payload, cost_usd: 0.02, spent_usd: 3 },
      });
      const other = appendEvent(meta, {
        ...tick,
        payload: { ...tick.payload, agent_id: "agent-2", cost_usd: null, spent_usd: 3 },
      });
      expect(other.spendUnknown).toBe(false);
      expect(selectSpend(other)?.spentUsd).toBe(3);
    },
  );

  it.each(["baseten", "fake"] as const)(
    "takes the latest total and brake from %s after an unpublished Meta tick (K18)",
    (provider) => {
      const state = mixed(provider);
      const other = appendEvent(state, {
        ...tick,
        payload: {
          ...tick.payload,
          agent_id: "agent-2",
          cost_usd: null,
          spent_usd: 3.5,
          brake: "blocked",
        },
      });
      expect(selectSpend(other)).toEqual({ spentUsd: 3.5, budgetUsd: 150, brake: "blocked" });
      expect(nextSlot(other)).toBe("B");
    },
  );

  it.each(["baseten", "fake"] as const)(
    "retains unknown Meta usage through a %s tick, until Meta reports a cost (K18)",
    (provider) => {
      const state = mixed(provider);
      const unknown = appendEvent(state, { ...tick, payload: { ...tick.payload, cost_usd: null } });
      const other = appendEvent(unknown, {
        ...tick,
        payload: { ...tick.payload, agent_id: "agent-2", cost_usd: null, spent_usd: 3.5 },
      });
      expect(other.spendUnknown).toBe(true);
      expect(other.usage?.spent_usd).toBe(3.5);
      expect(selectSpend(other)?.spentUsd).toBeNull();
      const known = appendEvent(other, tick);
      expect(known.spendUnknown).toBe(false);
      expect(selectSpend(known)?.spentUsd).toBe(12.4);
    },
  );

  it("treats an absent agent or unrecognized profile as Meta, regardless of the tick's API model", () => {
    for (const state of [
      through(1),
      { ...mixed("baseten"), agents: {} },
      {
        ...mixed("baseten"),
        agents: { "agent-1": { ...mixed("baseten").agents["agent-1"], model: "unknown-profile" } },
      },
    ]) {
      const next = appendEvent(state, {
        ...tick,
        payload: { ...tick.payload, model: "fake", cost_usd: null },
      });
      expect(next.spendUnknown).toBe(true);
      expect(selectSpend(next)?.spentUsd).toBeNull();
    }
  });
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

function mixed(provider: "baseten" | "fake") {
  const state = through(3);
  if (!state.run) throw new Error("Missing run fixture");
  const agent = state.agents["agent-1"];
  if (!agent) throw new Error("Missing agent fixture");
  return {
    ...state,
    run: {
      ...state.run,
      models: [
        {
          id: "meta",
          label: "Muse Spark",
          provider: "meta" as const,
          model: "meta-api",
          reasoning: null,
        },
        { id: "other", label: "Other", provider, model: "other-api", reasoning: null },
      ],
      slots: [
        { slot: "A" as const, default_model: "meta" },
        { slot: "B" as const, default_model: "other" },
      ],
    },
    agents: {
      "agent-1": { ...agent, model: "meta", state: "done" as const },
      "agent-2": {
        ...agent,
        agent_id: "agent-2",
        change_id: "change-2",
        slot: "B" as const,
        model: "other",
        state: "done" as const,
      },
    },
  };
}
