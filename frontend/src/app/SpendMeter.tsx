import type { UsageTick } from "@contracts";
import { Ban, CircleAlert } from "lucide-react";

import { selectSpend } from "../state";
import type { State } from "../state";

const captions: Record<UsageTick["brake"], string | null> = {
  ok: null,
  warn_50: "50% of the budget used",
  warn_80: "80% of the budget used",
  blocked: "New Meta runs are blocked: the budget is nearly spent",
};
const budgetFormat = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function SpendMeter({ state }: { state: State }) {
  const spend = selectSpend(state);
  if (!spend) return null;
  const caption = captions[spend.brake];
  const Icon = spend.brake === "blocked" ? Ban : CircleAlert;
  const total = spend.spentUsd === null ? "unknown" : `US$${spend.spentUsd.toFixed(2)}`;
  return (
    <div role="group" aria-label="Spend" className="shrink-0 text-muted">
      <p className="font-mono text-(length:--data-size) font-normal text-text">
        {`${total} of ${budgetFormat.format(spend.budgetUsd)}`}
      </p>
      <div
        role="status"
        aria-atomic="true"
        className="h-lh text-(length:--caption-size) whitespace-nowrap"
      >
        {caption && (
          <p className="flex items-center gap-1.5">
            <Icon aria-hidden="true" className="size-3.5 shrink-0" />
            {caption}
          </p>
        )}
      </div>
    </div>
  );
}
