import { selectMergeMode, selectSpend } from "../../state";
import type { State } from "../../state";

/** The ledger is cumulative, including ticks from the other provider. Never sum it per agent. */
export function selectBudgetLine(state: State): string | null {
  const spend = selectSpend(state);
  if (!spend) return null;
  const percentage =
    spend.spentUsd === null
      ? "unknown"
      : `${String(Math.round((spend.spentUsd / spend.budgetUsd) * 100))}%`;
  return `Budget ${percentage} · brake at 90%`;
}

export function selectMergeModeLine(state: State): string | null {
  const mode = selectMergeMode(state)?.mode;
  if (mode === "ask_me") return "Merge mode: Ask me";
  if (mode === "merge_when_safe") return "Merge mode: Merge alone when safe";
  return null;
}
