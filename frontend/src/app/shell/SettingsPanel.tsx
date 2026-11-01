import type { MergeMode } from "@contracts";
import { X } from "lucide-react";
import { selectMergeMode, type State } from "../../state";
import { mergeModePending, type MergeModeAttempt } from "./mergeModeAttempt";
import { SettingsFrame } from "./SettingsFrame";
import { focusRing } from "./shared";

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
    <SettingsFrame>
      <div className="mt-4 grid grid-cols-1 gap-3 @sm:grid-cols-2">
        {choices.map(([mode, title, body]) => {
          const marked = current?.mode === mode;
          return (
            <button
              key={mode}
              type="button"
              aria-pressed={marked}
              aria-disabled={pending || !onSetMergeMode || !current}
              onClick={(event) => {
                if (event.detail === 0 || marked || pending || !current) return;
                onSetMergeMode?.(mode);
              }}
              className={`group flex min-w-0 items-start gap-2.5 rounded-card border p-4 text-start ${marked ? "border-muted bg-page-raised" : "border-border bg-page"} aria-disabled:cursor-default aria-disabled:text-faint aria-[disabled=false]:hover:border-muted ${focusRing}`}
            >
              <span
                aria-hidden="true"
                className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-muted"
              >
                {marked && <span className="size-2 rounded-full bg-text" />}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-bold">{title}</span>
                <span className="mt-1.5 block text-sm leading-relaxed text-muted group-aria-disabled:text-faint">
                  {body}
                </span>
              </span>
            </button>
          );
        })}
      </div>
      {failure && (
        <p role="alert" className="mt-4 flex items-start gap-2 text-sm text-text">
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{failure}</span>
        </p>
      )}
      {!onSetMergeMode && (
        <p className="mt-4 text-sm text-muted">
          Setting the merge mode is not available in a replay.
        </p>
      )}
    </SettingsFrame>
  );
}
