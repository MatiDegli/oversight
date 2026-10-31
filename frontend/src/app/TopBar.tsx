import { Folder, GitBranch, Search } from "lucide-react";

import { selectDecisionInboxCount } from "../state";
import type { State } from "../state";
import { SpendMeter } from "./SpendMeter";
import "./palette/palette.css";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

/**
 * The top bar: name, repository, branch and commit, the review queue and spend. The sketches also
 * show "Earlier today" and settings; each joins the bar when its feature is built, so
 * the bar never carries a control that looks present and does nothing.
 */
export function TopBar({
  state,
  onOpenQueue,
  onOpenPalette,
  practice,
}: {
  practice?: { active: boolean; open: () => void };
  state: State;
  onOpenQueue?: () => void;
  onOpenPalette?: () => void;
}) {
  const ready = selectDecisionInboxCount(state);
  const commit = state.graph?.commit ?? state.run?.base_commit;
  return (
    <header className="flex h-(--top-bar-height) shrink-0 items-center gap-3.5 border-b border-divider bg-shell px-4.5">
      <span className="text-(length:--brand-size) font-bold">Oversight</span>
      {state.run && (
        <>
          <span aria-hidden="true" className="h-5 w-px shrink-0 bg-card-border" />
          <p className="flex min-w-0 items-center gap-3.5 text-sm text-muted">
            <span className="inline-flex min-w-0 items-center gap-2 font-semibold text-text">
              <Folder aria-hidden="true" className="size-4 shrink-0 text-muted" />
              <span className="truncate text-(length:--body-size)">{state.run.workspace}</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <GitBranch aria-hidden="true" className="size-4" /> main
            </span>
            {commit && (
              <span className="font-mono" title={commit}>
                {commit.slice(0, 7)}
              </span>
            )}
          </p>
        </>
      )}
      {onOpenPalette && (
        <button
          type="button"
          className="palette-trigger"
          aria-label="Find a function, file or command"
          aria-keyshortcuts="Control+k"
          aria-haspopup="dialog"
          onClick={onOpenPalette}
        >
          <Search aria-hidden="true" size={14} />
          <span>Find a function, file or command</span>
          <kbd>Ctrl K</kbd>
        </button>
      )}
      {practice && (
        <button
          type="button"
          data-practice-entry
          className={`shrink-0 text-sm underline ${focusRing}`}
          onClick={practice.open}
        >
          {practice.active ? "Open practice" : "Two-change practice"}
        </button>
      )}
      {/* Always mounted, so the counter's arrival is announced and nothing else moves. */}
      <div
        role="status"
        aria-label="Review queue"
        className="ml-auto flex shrink-0 items-center whitespace-nowrap"
      >
        <button
          type="button"
          disabled={onOpenQueue === undefined}
          onClick={onOpenQueue}
          title="Open decisions inbox"
          aria-label={`Decisions: ${String(ready)} pending. Open decisions inbox`}
          className={`inline-flex h-(--queue-counter-height) items-center gap-2.5 rounded-control border border-border bg-raised px-3.5 text-(length:--label-size) font-semibold ${ready > 0 ? "text-text" : "text-muted"} enabled:hover:bg-page-raised ${focusRing}`}
        >
          {ready === 0 ? (
            <span>Nothing needs you</span>
          ) : (
            <span>
              Decisions · <span className="font-mono tabular-nums">{ready}</span>
            </span>
          )}
        </button>
      </div>
      <SpendMeter state={state} showBrake={false} />
    </header>
  );
}
