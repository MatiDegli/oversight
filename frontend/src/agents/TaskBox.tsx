import type { StartAgent } from "@contracts";
import { X } from "lucide-react";
import { useId } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand";

import type { State } from "../state";
import { startBlock, taskBoxStore } from "./taskBox";
import type { TaskBoxModel } from "./taskBox";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
const primary = `inline-flex h-(--button-primary-height) items-center rounded-control bg-text px-4 text-(length:--label-size) font-semibold text-page not-aria-disabled:hover:bg-text-hover aria-disabled:cursor-not-allowed aria-disabled:bg-raised aria-disabled:text-faint ${focusRing}`;
const heading = "text-(length:--label-size) font-semibold";

const modifier =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent) ? "Cmd" : "Ctrl";

export interface TaskBoxViewProps {
  draft: string;
  pending: boolean;
  failure: string | null;
  /** Why no agent can start now, or null. */
  block: string | null;
  onDraft: (text: string) => void;
  onStart: () => void;
}

/**
 * The box as markup. The start button never takes the `disabled` attribute, and the box only turns
 * read-only while a start is pending: a disabled control drops keyboard focus, and the app never
 * moves focus by itself.
 */
export function TaskBoxView({
  draft,
  pending,
  failure,
  block,
  onDraft,
  onStart,
}: TaskBoxViewProps) {
  const id = useId();
  const canStart = !pending && draft.trim() !== "" && block === null;
  const line = pending ? "Starting the agent." : block;
  return (
    <section aria-labelledby={`${id}-heading`} className="flex flex-col gap-2.5">
      <h3 id={`${id}-heading`} className={heading}>
        New agent
      </h3>
      <textarea
        aria-label="Task for the new agent"
        value={draft}
        rows={3}
        readOnly={pending}
        placeholder="Describe the change you want"
        onChange={(event) => {
          onDraft(event.target.value);
        }}
        onKeyDown={(event) => {
          // Enter alone is a newline; Ctrl+Enter (Cmd+Enter on macOS) starts, except mid-composition.
          if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
          if (event.nativeEvent.isComposing || event.repeat) return;
          event.preventDefault();
          if (canStart) onStart();
        }}
        className={`resize-none rounded-card border border-control-edge bg-field p-(--input-padding) text-(length:--body-size) text-text placeholder:text-faint read-only:text-muted ${focusRing}`}
      />
      <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2">
        <button
          type="button"
          aria-disabled={!canStart}
          aria-describedby={`${id}-line`}
          onClick={() => {
            if (canStart) onStart();
          }}
          className={primary}
        >
          Start agent
        </button>
        <p className="text-(length:--caption-size) text-muted">
          <kbd className="rounded-keyboard-hint border border-control-edge px-1.5 font-mono">
            {modifier}
          </kbd>
          +
          <kbd className="rounded-keyboard-hint border border-control-edge px-1.5 font-mono">
            Enter
          </kbd>{" "}
          to start
        </p>
      </div>
      {/* Always mounted, so the reason is announced where it appears. */}
      <div role="status" id={`${id}-line`} className="text-sm text-muted">
        {line !== null && <p>{line}</p>}
      </div>
      {failure !== null && (
        <p role="alert" className="flex items-start gap-2 text-sm text-failed-text">
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" /> {failure}
        </p>
      )}
    </section>
  );
}

export interface TaskBoxProps {
  state: State;
  /** Absent in a recorded run, which has no backend and starts nothing. */
  onStart?: (body: StartAgent) => Promise<unknown>;
  /** The draft and request; the app's one box by default. */
  store?: StoreApi<TaskBoxModel>;
}

/** The agents panel's "New agent" section: a task box and a Start agent button. */
export function TaskBox({ state, onStart, store = taskBoxStore }: TaskBoxProps) {
  const draft = useStore(store, (model) => model.draft);
  const pending = useStore(store, (model) => model.pending);
  const failure = useStore(store, (model) => model.failure);
  if (!onStart) {
    return (
      <section aria-label="New agent" className="flex flex-col gap-2.5">
        <h3 className={heading}>New agent</h3>
        <p className="text-sm text-muted">Recorded run: agents cannot be started here.</p>
      </section>
    );
  }
  return (
    <TaskBoxView
      draft={draft}
      pending={pending}
      failure={failure}
      block={startBlock(state)}
      onDraft={store.getState().setDraft}
      onStart={() => {
        void store.getState().start(onStart);
      }}
    />
  );
}
