import type { Budget } from "@contracts";
import { formatBudget, formatUsd, type BudgetMark, type ModelWait } from "../../state";

export function elapsed(ms: number): string {
  const s = Math.floor(Math.max(0, ms) / 1000);
  return s < 60
    ? `${String(s)} s`
    : `${String(Math.floor(s / 60))} min ${String(s % 60).padStart(2, "0")} s`;
}
export const toolTime = (ms: number) =>
  ms < 1000 ? `${String(ms)} ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : elapsed(ms);
export const causeWords: Record<ModelWait["cause"], string> = {
  rate_limited: "Rate limited",
  server_error: "Server error",
  connection: "Connection failed",
  timeout: "Timed out",
  refused: "Refused",
};
export function timeWords(ms: number, bound: number) {
  return {
    used: `${String(Math.floor(ms / 60_000))} min`,
    bound: bound % 60 === 0 ? `${String(bound / 60)} min` : `${String(bound)} s`,
  };
}
export function budgetWords(mark: BudgetMark, budget: Budget, unknown: boolean) {
  const blocked = mark.brake === "blocked";
  const percent = blocked
    ? mark.budget_usd > 0
      ? Math.floor((mark.spent_usd / mark.budget_usd) * 100)
      : null
    : Math.round((budget.warn_at[mark.brake === "warn_50" ? 0 : 1] ?? 0) * 100);
  return {
    lead: percent === null ? "Budget" : `Budget at ${String(percent)}%`,
    detail: `${unknown ? "" : `${formatUsd(mark.spent_usd)} of ${formatBudget(mark.budget_usd)} · `}${blocked ? "new Meta runs are refused" : `new Meta runs stop at ${String(Math.round(budget.block_at * 100))}%`}`,
  };
}
