import type { Slot, StartAgent } from "@contracts";
import { Check, Cpu, X } from "lucide-react";
import { useId } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand";

import type { State } from "../state";
import {
  holderReason,
  selectedSlot,
  slotChoice,
  slotHolder,
  startBlock,
  taskBoxStore,
} from "./taskBox";
import type { TaskBoxModel } from "./taskBox";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
const primary = `inline-flex h-(--button-primary-height) items-center rounded-control bg-text px-4 text-(length:--label-size) font-semibold text-page not-aria-disabled:hover:bg-text-hover aria-disabled:cursor-not-allowed aria-disabled:bg-raised aria-disabled:text-faint ${focusRing}`;
const heading = "text-(length:--label-size) font-semibold";
const chip =
  "inline-flex h-(--chip-height) items-center gap-1.5 rounded-control border border-page-border bg-page-raised px-2 text-(length:--data-size) whitespace-nowrap text-text";

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
  slots?: { slot: Slot; label: string; holder: string | null }[];
  selected?: Slot | null;
  onSlot?: (slot: Slot) => void;
  /** The one model on offer when there is no slot choice, such as "Muse Spark". */
  model?: string;
  /** The run's step limit, shown as "Up to 30 steps". */
  maxSteps?: number;
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
  slots = [],
  selected = null,
  onSlot,
  model,
  maxSteps,
}: TaskBoxViewProps) {
  const id = useId();
  const canStart = !pending && draft.trim() !== "" && block === null;
  const line = pending ? "Starting the agent." : block;
  return (
    <section aria-labelledby={`${id}-heading`} className="flex flex-col gap-2.5">
      {/* The sketch names the box by its placeholder alone; the heading stays for screen readers. */}
      <h3 id={`${id}-heading`} className={`${heading} sr-only`}>
        New agent
      </h3>
      <div className="flex flex-col gap-3 rounded-card border border-control-edge bg-field p-3 has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-offset-2 has-[textarea:focus-visible]:outline-text">
        <textarea
          aria-label="Task for the new agent"
          value={draft}
          rows={3}
          readOnly={pending}
          placeholder="Describe a task. Each agent works in its own worktree, and nothing reaches main without your approval."
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
          // The box around it draws the field and, with the keyboard, the focus ring.
          className="resize-none bg-transparent p-1 text-(length:--body-size) text-text placeholder:text-faint read-only:text-muted focus:outline-none"
        />
        {slots.length === 2 && (
          <div role="group" aria-label="Agent slot" className="flex flex-wrap gap-2">
            {slots.map(({ slot, label, holder }) => (
              <button
                key={slot}
                type="button"
                aria-pressed={selected === slot}
                aria-disabled={pending || holder !== null}
                onClick={() => {
                  if (!pending && holder === null) onSlot?.(slot);
                }}
                className={`min-w-0 flex-1 rounded-control border border-control-edge p-2 text-left text-(length:--caption-size) aria-pressed:bg-raised aria-disabled:cursor-not-allowed ${selected === slot ? "aria-pressed:border-text" : ""} ${focusRing}`}
              >
                <span className={slot === "A" ? "text-agent-a-text" : "text-agent-b-text"}>
                  {slot}
                </span>{" "}
                {label}
                {selected === slot && (
                  <Check aria-hidden="true" className="ml-1 inline-block size-4 text-text" />
                )}
                {holder !== null && <span className="block text-muted">{holder}</span>}
              </button>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          {model !== undefined && (
            <span aria-label={`Model: ${model}`} className={chip}>
              <Cpu aria-hidden="true" className="size-4 shrink-0 text-muted" />
              {model}
            </span>
          )}
          {maxSteps !== undefined && (
            <span className={chip}>
              Up to {maxSteps} {maxSteps === 1 ? "step" : "steps"}
            </span>
          )}
          <button
            type="button"
            aria-disabled={!canStart}
            aria-describedby={`${id}-line`}
            onClick={() => {
              if (canStart) onStart();
            }}
            className={`${primary} ml-auto`}
          >
            Start agent
          </button>
        </div>
      </div>
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
      {/* Always mounted, so the reason is announced where it appears. */}
      <div role="status" id={`${id}-line`} className="text-sm text-muted">
        {line !== null && <p>{line}</p>}
      </div>
      {failure !== null && (
        <p role="alert" className="flex items-start gap-2 text-sm text-text">
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" /> {failure}
        </p>
      )}
    </section>
  );
}

export interface TaskBoxProps {
  state: State;
  /** Absent in a replay, which has no backend and starts nothing. */
  onStart?: (body: StartAgent) => Promise<unknown>;
  /** What a replay plays, such as "hand-written demo fixture"; absent in a live run. */
  replayOf?: string;
  /** The draft and request; the app's one box by default. */
  store?: StoreApi<TaskBoxModel>;
}

/** The agents panel's "New agent" section: a task box and a Start agent button. */
export function TaskBox({ state, onStart, replayOf, store = taskBoxStore }: TaskBoxProps) {
  const draft = useStore(store, (model) => model.draft);
  const pending = useStore(store, (model) => model.pending);
  const failure = useStore(store, (model) => model.failure);
  const choice = useStore(store, (model) => model.choice);
  const selected = selectedSlot(state, choice);
  const slots = (state.run?.slots ?? []).map((info) => {
    const holder = slotHolder(state, info.slot);
    return {
      slot: info.slot,
      label:
        state.run?.models.find((model) => model.id === info.default_model)?.label ??
        info.default_model,
      holder: holder ? holderReason(holder) : null,
    };
  });
  if (!onStart) {
    return (
      <section aria-label="New agent" className="flex flex-col gap-2.5">
        <h3 className={heading}>New agent</h3>
        {/* What the log is comes from its source: only a real recording may call itself one. */}
        <p className="text-sm text-muted">
          {`${replayOf === undefined ? "Replay" : `Replay of a ${replayOf}`}: agents cannot be started here.`}
        </p>
      </section>
    );
  }
  return (
    <TaskBoxView
      draft={draft}
      pending={pending}
      failure={failure}
      block={startBlock(state, selected)}
      model={slots.length === 1 ? slots[0]?.label : undefined}
      maxSteps={state.run?.limits.max_steps}
      slots={slots}
      selected={selected}
      onSlot={(slot) => {
        store.getState().choose(slotChoice(state, slot));
      }}
      onDraft={store.getState().setDraft}
      onStart={() => {
        void store.getState().start(onStart, slots.length === 2 ? selected : undefined);
      }}
    />
  );
}
