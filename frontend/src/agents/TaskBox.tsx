import type { EnqueueTask, QueueRevision, QueuedTask, Slot, StartAgent } from "@contracts";
import { Check, Cpu, X } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode, Ref } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand";

import { selectMergeMode, type State } from "../state";
import { ModelMenu } from "./ModelMenu";
import {
  exampleTasks,
  holderReason,
  offeredModels,
  pickedModel,
  selectQueuedTasks,
  selectedSlot,
  slotChoice,
  slotHolder,
  startBlock,
  taskBoxStore,
} from "./taskBoxModel";
import type { TaskBoxModel } from "./taskBoxModel";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
const primary = `inline-flex h-(--button-primary-height) items-center rounded-control bg-text px-4 text-(length:--label-size) font-semibold text-page not-aria-disabled:hover:bg-text-hover aria-disabled:cursor-not-allowed aria-disabled:bg-raised aria-disabled:text-faint ${focusRing}`;
const heading = "text-(length:--label-size) font-semibold";
const chip =
  "inline-flex h-(--chip-height) items-center gap-1.5 rounded-control border border-page-border bg-page-raised px-2 text-(length:--data-size) whitespace-nowrap text-text";

const key = "rounded-keyboard-hint border border-control-edge px-1.5 font-mono";
const queueStates: Record<string, string> = {
  queued: "Waiting for a slot",
  starting: "Starting",
  blocked: "Blocked",
};

/** The person's queued tasks under the box, oldest first, each cancellable while it waits. */
function QueuedTasks({ queued }: { queued: NonNullable<TaskBoxViewProps["queued"]> }) {
  const id = useId();
  const [failure, setFailure] = useState<string | null>(null);
  return (
    <section aria-labelledby={`${id}-queued`} className="task-box-queue flex flex-col gap-1.5">
      <h4 id={`${id}-queued`} className="text-(length:--caption-size) font-semibold text-muted">
        Queued ({queued.length})
      </h4>
      <ol className="flex flex-col gap-1">
        {queued.map(({ item, onCancel }) => (
          <li
            key={item.queued_task_id}
            className="flex min-h-9 items-center gap-2 rounded-control border border-page-border bg-page-raised px-2 text-(length:--data-size)"
          >
            <span className="min-w-0 flex-1 truncate text-text" title={item.agent?.task}>
              {item.agent?.task}
            </span>
            <span className="shrink-0 text-muted">
              {queueStates[item.state] ?? item.state}
              {item.state === "blocked" && item.detail ? `: ${item.detail}` : ""}
            </span>
            {onCancel && item.state !== "starting" && (
              <button
                type="button"
                aria-label={`Cancel queued task: ${item.agent?.task ?? ""}`}
                onClick={() => {
                  setFailure(null);
                  onCancel().catch((error: unknown) => {
                    setFailure(
                      `Could not cancel the queued task: ${error instanceof Error ? error.message : "no answer"}`,
                    );
                  });
                }}
                className={`shrink-0 rounded-control px-1.5 text-muted hover:text-text ${focusRing}`}
              >
                Cancel
              </button>
            )}
          </li>
        ))}
      </ol>
      {failure !== null && <p className="text-sm text-text">{failure}</p>}
    </section>
  );
}

const steps = (count: number) => `${String(count)} ${count === 1 ? "step" : "steps"}`;

const modifier =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent) ? "Cmd" : "Ctrl";

export interface TaskBoxViewProps {
  onOpenSettings?: () => void;
  actionAccessory?: ReactNode;
  inputRef?: Ref<HTMLTextAreaElement>;
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
  /** With `onModel`, the chip opens a menu of these models, `modelId` checked. */
  models?: { id: string; label: string }[];
  modelId?: string;
  onModel?: (id: string) => void;
  /** Opens Settings from the model menu and the step chip; absent where there is no Settings. */
  onSettings?: () => void;
  /** The run's step limit, shown as "Up to 30 steps". */
  maxSteps?: number;
  /** Queues the draft for the next free slot; absent where the server cannot queue. */
  onQueue?: () => void;
  /** Whether the pending request queues rather than starts. */
  queueing?: boolean;
  /** The last queueing's confirmation. */
  notice?: string | null;
  /** The person's tasks waiting for a slot, each with its cancel. */
  queued?: { item: QueuedTask; onCancel?: () => Promise<unknown> }[];
  /** Tasks a click puts in the empty box, never starting one. */
  examples?: { label: string; task: string }[];
  /** Under Merge alone when safe, the box does not promise that every change waits for approval. */
  mergeAlone?: boolean;
}

/**
 * The box as markup. The start button never takes the `disabled` attribute, and the box only turns
 * read-only while a start is pending: a disabled control drops keyboard focus, and the app never
 * moves focus by itself.
 */
export function TaskBoxView({
  inputRef,
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
  models = [],
  modelId,
  onModel,
  onSettings,
  maxSteps,
  actionAccessory,
  onOpenSettings,
  onQueue,
  queueing = false,
  notice = null,
  queued = [],
  examples = [],
  mergeAlone = false,
}: TaskBoxViewProps) {
  const id = useId();
  const canStart = !pending && draft.trim() !== "" && block === null;
  const canQueue = !pending && draft.trim() !== "" && onQueue !== undefined;
  const line = pending
    ? queueing
      ? "Queueing the task."
      : "Starting the agent."
    : block !== null && onQueue
      ? `${block} ${modifier}+Enter queues the task.`
      : (block ?? notice);
  return (
    <section aria-labelledby={`${id}-heading`} className="task-box flex flex-col gap-2.5">
      {/* The sketch names the box by its placeholder alone; the heading stays for screen readers. */}
      <h3 id={`${id}-heading`} className={`${heading} sr-only`}>
        New agent
      </h3>
      <div className="task-box-field flex flex-col gap-3 rounded-card border border-control-edge bg-field p-3 has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-offset-2 has-[textarea:focus-visible]:outline-text">
        <textarea
          ref={inputRef}
          id={`${id}-task`}
          aria-label="Task for the new agent"
          value={draft}
          rows={3}
          readOnly={pending}
          placeholder={`Describe a task. Each agent works in its own worktree, and ${mergeAlone ? "a change that passes every merge rule merges by itself; the rest waits for your approval" : "nothing reaches main without your approval"}.`}
          onChange={(event) => {
            onDraft(event.target.value);
          }}
          onKeyDown={(event) => {
            // Enter starts and Shift+Enter is a newline; Ctrl+Enter (Cmd+Enter on macOS) queues.
            // Never mid-composition, where Enter confirms the input method's text.
            if (event.key !== "Enter" || event.shiftKey || event.altKey) return;
            if (event.nativeEvent.isComposing) return;
            event.preventDefault();
            if (event.repeat) return;
            if ((event.ctrlKey || event.metaKey) && onQueue) {
              if (canQueue) onQueue();
            } else if (canStart) onStart();
          }}
          // The box around it draws the field and, with the keyboard, the focus ring.
          className="resize-none bg-transparent p-1 text-(length:--body-size) text-text placeholder:text-faint read-only:text-muted focus:outline-none"
        />
        {slots.length === 2 && (
          <div
            role="group"
            aria-label="Agent slot"
            className="task-box-models flex flex-wrap gap-2"
          >
            {slots.map(({ slot, label, holder }) => (
              <button
                key={slot}
                data-slot-card={slot}
                type="button"
                aria-pressed={selected === slot}
                aria-disabled={pending || holder !== null}
                onClick={() => {
                  if (!pending && holder === null) onSlot?.(slot);
                }}
                className={`min-w-0 flex-1 rounded-control border border-control-edge p-2 text-left text-(length:--caption-size) aria-pressed:bg-raised aria-disabled:cursor-not-allowed ${selected === slot ? "aria-pressed:border-text" : ""} ${focusRing}`}
              >
                {label}
                {selected === slot && (
                  <Check aria-hidden="true" className="ml-1 inline-block size-4 text-text" />
                )}
                {holder !== null && <span className="block min-h-[2lh] text-muted">{holder}</span>}
              </button>
            ))}
          </div>
        )}
        <div className="task-box-actions flex flex-wrap items-center gap-1.5">
          {model !== undefined &&
            (onModel && models.length > 0 ? (
              <ModelMenu
                label={model}
                models={models}
                current={modelId}
                locked={pending}
                chipClass={`${chip} ${focusRing}`}
                onModel={onModel}
                onSettings={onSettings}
              />
            ) : (
              <span aria-label={`Model: ${model}`} className={chip}>
                <Cpu aria-hidden="true" className="size-4 shrink-0 text-muted" />
                {model}
              </span>
            ))}
          {maxSteps !== undefined &&
            (onSettings ? (
              // Wrapped like the model menu, so Start agent stays the row's one direct button.
              <span className="inline-flex">
                <button
                  type="button"
                  title="Change the step limit in Settings"
                  aria-label={`Up to ${steps(maxSteps)}. Change the step limit in Settings`}
                  onClick={onSettings}
                  className={`${chip} hover:bg-raised ${focusRing}`}
                >
                  Up to {steps(maxSteps)}
                </button>
              </span>
            ) : (
              <span className={chip}>Up to {steps(maxSteps)}</span>
            ))}
          {actionAccessory}
          <button
            type="button"
            aria-disabled={!canStart}
            aria-describedby={`${id}-line`}
            onClick={() => {
              if (canStart) onStart();
            }}
            className={`${primary} ${actionAccessory ? "" : "ml-auto"}`}
          >
            Start agent
          </button>
        </div>
      </div>
      {draft === "" && !pending && examples.length > 0 && (
        <div
          role="group"
          aria-label="Example tasks"
          className="task-box-examples flex flex-wrap items-center gap-1.5 text-(length:--caption-size)"
        >
          <span className="text-muted">Try:</span>
          {examples.map((example) => (
            <button
              key={example.label}
              type="button"
              title={example.task}
              onClick={() => {
                // The person's click: the task goes in the box, which takes the focus; nothing starts.
                document.getElementById(`${id}-task`)?.focus();
                onDraft(example.task);
              }}
              className={`min-h-8 rounded-control border border-page-border bg-page-raised px-2 text-text hover:bg-raised ${focusRing}`}
            >
              {example.label}
            </button>
          ))}
        </div>
      )}
      <p className="task-box-hint text-(length:--caption-size) text-muted">
        <kbd className={key}>Enter</kbd> to start
        {onQueue && (
          <>
            {" · "}
            <kbd className={key}>{modifier}</kbd>+<kbd className={key}>Enter</kbd> to queue
          </>
        )}
        {" · "}
        <kbd className={key}>Shift</kbd>+<kbd className={key}>Enter</kbd> for a new line
      </p>
      {queued.length > 0 && <QueuedTasks queued={queued} />}
      {/* Always mounted, so the reason is announced where it appears. */}
      <div role="status" id={`${id}-line`} className="min-h-[2lh] text-sm text-muted">
        {line !== null && <p>{line}</p>}
      </div>
      {failure !== null && (
        <p role="alert" className="flex items-start gap-2 text-sm text-text">
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" /> {failure}
        </p>
      )}
      {failure !== null && onOpenSettings && (
        <button
          type="button"
          onClick={onOpenSettings}
          className={`self-start min-h-10 text-sm text-text underline underline-offset-4 ${focusRing}`}
        >
          Open settings
        </button>
      )}
    </section>
  );
}

export interface TaskBoxProps {
  onOpenSettings?: () => void;
  actionAccessory?: ReactNode;
  /** Only an explicit action requests focus through this ref. */
  inputRef?: Ref<HTMLTextAreaElement>;
  state: State;
  /** Absent in a replay, which has no backend and starts nothing. */
  onStart?: (body: StartAgent) => Promise<unknown>;
  /** Queues a task for the next free slot; absent in a replay. */
  onQueue?: (body: EnqueueTask) => Promise<unknown>;
  onCancelQueued?: (id: string, body: QueueRevision) => Promise<unknown>;
  /** What a replay plays, such as "hand-written demo fixture"; absent in a live run. */
  replayOf?: string;
  /** The draft and request; the app's one box by default. */
  store?: StoreApi<TaskBoxModel>;
}

/** The agents panel's "New agent" section: a task box and a Start agent button. */
export function TaskBox({
  state,
  onStart,
  replayOf,
  inputRef,
  store = taskBoxStore,
  actionAccessory,
  onOpenSettings,
  onQueue,
  onCancelQueued,
}: TaskBoxProps) {
  const draft = useStore(store, (model) => model.draft);
  const pending = useStore(store, (model) => model.pending);
  const queueing = useStore(store, (model) => model.queueing);
  const notice = useStore(store, (model) => model.notice);
  const failure = useStore(store, (model) => model.failure);
  const failureCode = useStore(store, (model) => model.failureCode);
  const choice = useStore(store, (model) => model.choice);
  const pick = useStore(store, (model) => model.model);
  const selected = selectedSlot(state, choice);
  const slots = (state.run?.slots ?? []).map((info) => {
    const holder = slotHolder(state, info.slot);
    return {
      slot: info.slot,
      label:
        state.run?.models.find((model) => model.id === info.default_model)?.label ??
        info.default_model,
      holder: holder ? holderReason(state, holder) : null,
    };
  });
  const modelChoice = new Set(state.run?.slots.map((info) => info.default_model)).size > 1;
  const showSlotCards = modelChoice && slots.length === 2;
  // Slot cards choose the model themselves; otherwise the chip's pick, if any, goes with the task.
  const models = offeredModels(state);
  const picked = showSlotCards ? null : pickedModel(state, pick);
  const modelId = picked ?? state.run?.slots[0]?.default_model;
  if (!onStart) {
    return (
      <section aria-label="New agent" className="flex flex-col gap-2.5">
        <h3 className={heading}>New agent</h3>
        {/* What the log is comes from its source: only a real recording may call itself one. */}
        <p className="text-sm text-muted">
          {`${replayOf === undefined ? "Replay" : `Replay of a ${replayOf}`}: agents cannot be started here.`}
        </p>
        {actionAccessory && <div className="w-max max-w-full self-start">{actionAccessory}</div>}
      </section>
    );
  }
  return (
    <TaskBoxView
      actionAccessory={actionAccessory}
      inputRef={inputRef}
      draft={draft}
      pending={pending}
      failure={failure}
      onOpenSettings={failureCode === "not_configured" ? onOpenSettings : undefined}
      block={startBlock(state, selected)}
      model={
        showSlotCards
          ? undefined
          : (models.find((model) => model.id === modelId)?.label ?? slots[0]?.label)
      }
      models={models}
      modelId={modelId}
      onModel={(model) => {
        store.getState().pickModel({ model, runId: state.run?.run_id });
      }}
      onSettings={onOpenSettings}
      maxSteps={state.run?.limits.max_steps}
      slots={showSlotCards ? slots : []}
      selected={selected}
      onSlot={(slot) => {
        store.getState().choose(slotChoice(state, slot));
      }}
      onDraft={store.getState().setDraft}
      onStart={() => {
        void store.getState().start(onStart, showSlotCards ? selected : undefined, picked);
      }}
      onQueue={
        onQueue && state.run
          ? () => {
              void store.getState().queue(onQueue, showSlotCards ? selected : undefined, picked);
            }
          : undefined
      }
      queueing={queueing}
      notice={notice}
      examples={exampleTasks(state)}
      mergeAlone={selectMergeMode(state)?.mode === "merge_when_safe"}
      queued={selectQueuedTasks(state).map((item) => ({
        item,
        onCancel: onCancelQueued
          ? () => onCancelQueued(item.queued_task_id, { revision: item.revision })
          : undefined,
      }))}
    />
  );
}
