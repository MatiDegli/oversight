import type { UsageTick } from "@contracts";
import { Ban, CircleAlert } from "lucide-react";

import { formatBudget, formatUsd, selectSpend } from "../state";
import type { State } from "../state";

const captions: Record<UsageTick["brake"], string | null> = {
  ok: null,
  warn_50: "50% of the budget used",
  warn_80: "80% of the budget used",
  blocked: "New Meta runs are blocked: the budget is nearly spent",
};
/**
 * "Spent US$0.47 of US$150" on the bar's own line. The group is anchored to the bar's right
 * edge, and a brake caption (icon and words) comes before it on the same line, so the total never
 * moves when the caption appears.
 */
export function SpendMeter({ state, showBrake = true }: { state: State; showBrake?: boolean }) {
  const spend = selectSpend(state);
  if (!spend) return null;
  const caption = captions[spend.brake];
  const Icon = spend.brake === "blocked" ? Ban : CircleAlert;
  const total = spend.spentUsd === null ? "unknown" : formatUsd(spend.spentUsd);
  return (
    <div
      role="group"
      aria-label="Spend"
      className="flex shrink-0 items-center gap-3.5 text-sm whitespace-nowrap text-muted"
    >
      <div role="status" aria-atomic="true" className="text-(length:--caption-size)">
        {showBrake && caption && (
          <p className="flex items-center gap-1.5">
            <Icon aria-hidden="true" className="size-3.5 shrink-0" />
            {caption}
          </p>
        )}
      </div>
      <p>
        Spent{" "}
        <span className="font-mono text-(length:--data-size) font-bold text-text">{total}</span> of{" "}
        <span className="font-mono text-(length:--data-size)">{formatBudget(spend.budgetUsd)}</span>
      </p>
    </div>
  );
}
