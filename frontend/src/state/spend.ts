import type { UsageTick } from "@contracts";

import type { State } from "./types";

export interface Spend {
  spentUsd: number | null;
  budgetUsd: number;
  brake: UsageTick["brake"];
}

/** The latest ledger total is already cumulative across agents; never add ticks together. */
export function selectSpend(state: State): Spend | null {
  const budget = state.run?.budget;
  if (!budget || budget.budget_usd <= 0) return null;
  if (state.usage) {
    return {
      spentUsd: state.spendUnknown ? null : state.usage.spent_usd,
      budgetUsd: state.usage.budget_usd,
      brake: state.usage.brake,
    };
  }

  // Before the first tick, the run carries the ledger balance and configured thresholds.
  // Only Meta profiles are braked, even when other providers have a configured budget.
  const share = budget.spent_usd / budget.budget_usd;
  const brake = !state.run?.models.some((model) => model.provider === "meta")
    ? "ok"
    : share > budget.block_at
      ? "blocked"
      : share >= (budget.warn_at[1] ?? Infinity)
        ? "warn_80"
        : share >= (budget.warn_at[0] ?? Infinity)
          ? "warn_50"
          : "ok";
  return { spentUsd: budget.spent_usd, budgetUsd: budget.budget_usd, brake };
}
