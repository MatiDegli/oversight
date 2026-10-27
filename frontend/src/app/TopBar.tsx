import { Folder, GitBranch, History, Search, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { State } from "../state";
import { SpendMeter } from "./SpendMeter";
import { readyCount } from "./view";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

/**
 * A control whose feature is not built yet. It is shown the way the design places it, plainly
 * unavailable: a disabled control in the faint color with no hover, and the reason in its title. It
 * never looks live, and it has no shortcut hint, since a hint promises a key that does nothing.
 */
function Unbuilt({ label, icon: Icon }: { label: string; icon: LucideIcon }) {
  return (
    <button
      type="button"
      disabled
      aria-label={label}
      title={`${label}: not built yet`}
      className="grid size-(--button-small-height) cursor-default place-items-center rounded-control text-faint opacity-60"
    >
      <Icon aria-hidden="true" className="size-5" />
    </button>
  );
}

function UnbuiltSearch() {
  return (
    <div
      role="search"
      className="ml-3.5 flex h-(--search-box-height) w-105 min-w-0 items-center gap-2.5 rounded-card border border-card-border bg-field px-3"
    >
      <Search aria-hidden="true" className="size-4 shrink-0 text-faint" />
      <input
        type="search"
        disabled
        aria-label="Find a function or file"
        title="Search: not built yet"
        placeholder="Find a function or file"
        className="min-w-0 flex-1 cursor-default bg-transparent text-sm text-faint placeholder:text-faint"
      />
    </div>
  );
}

/** The top bar: name, repository, branch and commit, search, the review queue, spend, history, settings. */
export function TopBar({ state, onOpenQueue }: { state: State; onOpenQueue?: () => void }) {
  const ready = readyCount(state);
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
      <UnbuiltSearch />
      {/* Always mounted, so the counter's arrival is announced and nothing else moves. */}
      <div role="status" aria-label="Review queue" className="ml-auto flex items-center">
        {ready > 0 && (
          <button
            type="button"
            disabled={onOpenQueue === undefined}
            onClick={onOpenQueue}
            title="Open the next change in review"
            className={`inline-flex h-(--queue-counter-height) items-center gap-2.5 rounded-control border border-border bg-raised px-3.5 text-(length:--label-size) font-semibold text-text enabled:hover:bg-page-raised ${focusRing}`}
          >
            <span aria-hidden="true" className="size-(--status-lamp-size) rounded-full bg-text" />
            {`${String(ready)} ${ready === 1 ? "change" : "changes"} ready for review`}
          </button>
        )}
      </div>
      <SpendMeter state={state} />
      <div className="flex items-center">
        <Unbuilt label="Earlier today" icon={History} />
        <Unbuilt label="Settings" icon={Settings} />
      </div>
    </header>
  );
}
