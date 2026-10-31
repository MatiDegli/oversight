import type { MergeMode } from "@contracts";
import { X } from "lucide-react";
import { selectMergeMode, type State } from "../../state";
import { focusRing } from "./shared";

/** Kept by App so leaving the tab neither loses a refusal nor admits another request. */
export type MergeModeAttempt = {
  run: State["run"];
  revision: number;
} & ({ phase: "pending"; responseRevision?: number } | { phase: "failed"; message: string });

export function mergeModePending(state: State, attempt?: MergeModeAttempt): boolean {
  return (
    attempt?.run === state.run &&
    attempt.phase === "pending" &&
    (attempt.responseRevision === undefined ||
      (selectMergeMode(state)?.revision ?? -1) < attempt.responseRevision)
  );
}

const choices: readonly [MergeMode, string, string][] = [
  ["ask_me", "Ask me", "Every change waits for your decision."],
  [
    "merge_when_safe",
    "Merge alone when safe",
    "A change merges by itself only when every rule holds. Otherwise it waits for you.",
  ],
];

export function SettingsPanel({
  state,
  attempt,
  onSetMergeMode,
}: {
  state: State;
  attempt?: MergeModeAttempt;
  onSetMergeMode?: (mode: MergeMode) => void;
}) {
  const current = selectMergeMode(state);
  const pending = mergeModePending(state, attempt);
  const failure =
    attempt?.run === state.run &&
    attempt.revision === current?.revision &&
    attempt.phase === "failed"
      ? attempt.message
      : null;
  return (
    <section
      aria-labelledby="merge-mode-heading"
      className="@container min-h-0 w-full min-w-0 overflow-y-auto p-8"
    >
      <h2 id="merge-mode-heading" className="text-(length:--title-size) font-bold">
        Merge mode
      </h2>
      <p className="mt-1 text-sm text-muted">Who lets a finished change into main.</p>
      <div className="mt-4 grid grid-cols-1 gap-3 @sm:grid-cols-2">
        {choices.map(([mode, title, body]) => {
          const marked = current?.mode === mode;
          return (
            <button
              key={mode}
              type="button"
              aria-pressed={marked}
              aria-disabled={!onSetMergeMode || !current}
              disabled={pending}
              onClick={(event) => {
                if (event.detail === 0 || marked || pending || !current) return;
                onSetMergeMode?.(mode);
              }}
              className={`flex min-w-0 items-start gap-2.5 rounded-card border p-4 text-start ${marked ? "border-muted bg-page-raised" : "border-border bg-page"} enabled:hover:border-muted ${focusRing}`}
            >
              <span
                aria-hidden="true"
                className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-muted"
              >
                {marked && <span className="size-2 rounded-full bg-text" />}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-bold">{title}</span>
                <span className="mt-1.5 block text-sm leading-relaxed text-muted">{body}</span>
              </span>
            </button>
          );
        })}
      </div>
      {failure && (
        <p role="alert" className="mt-4 flex items-start gap-2 text-sm text-muted">
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{failure}</span>
        </p>
      )}
      {!onSetMergeMode && (
        <p className="mt-4 text-sm text-muted">
          Setting the merge mode is not available in a replay.
        </p>
      )}
    </section>
  );
}
