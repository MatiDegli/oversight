import type { UsageTick } from "@contracts";

import type { State } from "./types";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  currencyDisplay: "code",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** `US$150.00`: the code Meta's invoices use, and no space, as the total is written. */
export const formatUsd = (amount: number) =>
  money.format(amount).replace("USD", "US$").replace(/\s/g, "");

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
