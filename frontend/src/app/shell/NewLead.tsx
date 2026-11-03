import type { StartLead } from "@contracts";
import { Info, Plus, X } from "lucide-react";
import { useId, type Ref } from "react";
import { useStore } from "zustand";
import type { State } from "../../state";
// eslint-disable-next-line no-restricted-imports -- K2a owns this form's persistent draft; no shared barrel change.
import { leadDraftStore } from "../../agents/leadDraftModel";
// eslint-disable-next-line no-restricted-imports -- The run's retained lead limits are projected in the same front.
import { leadCapacity, selectLeadLimits } from "../../agents/chatLeadSelectors";
import { ChatMergeChip } from "./ChatMerge";
import "./chatLeads.css";

function LeadLimits({ state }: { state: State }) {
  const limits = selectLeadLimits(state);
  const n = (value: number) => (
    <span className="font-mono">
      {value.toLocaleString("en-US", { maximumSignificantDigits: 21 })}
    </span>
  );
  return (
    <div className="new-lead-limits">
      <div className="new-lead-limit-heading">
        <Info size={13} aria-hidden="true" />
        {limits && <h2>It starts with these limits</h2>}
        <p>Set before Oversight starts; every lead gets the same.</p>
      </div>
      {limits && (
        <div className="new-lead-limit-cards">
          <div>
            <h3>This lead</h3>
            <p>{n(limits.turns)} turns</p>
            <p>{n(limits.plans)} plans</p>
          </div>
          <div>
            <h3>Each turn</h3>
            <p>{n(limits.tool_calls)} tool calls</p>
            <p>{n(limits.model_calls)} model calls</p>
            <p>{n(limits.wall_clock_s)} s</p>
          </div>
          <div>
            <h3>Each plan</h3>
            <p>{n(limits.subtasks)} subtasks</p>
            <p>{n(limits.task_chars)} characters a task</p>
          </div>
        </div>
      )}
    </div>
  );
}

export function NewLead({
  state,
  onStart,
  onClose,
  onTaskInstead,
  onOpenSettings,
  inputRef,
}: {
  state: State;
  onStart?: (body: StartLead) => Promise<unknown>;
  onClose: () => void;
  onTaskInstead: () => void;
  onOpenSettings?: () => void;
  inputRef: Ref<HTMLTextAreaElement>;
}) {
  const id = useId();
  const { draft, pending, failure } = useStore(leadDraftStore);
  const { active, max, atCap } = leadCapacity(state);
  const canStart = Boolean(onStart && draft.trim() && !pending && !atCap);
  return (
    <div className="chat-empty new-lead">
      <div>
        <h1>What should the new lead work on?</h1>
        <p className="chat-start-explanation">
          When work is worth splitting across several agents, a lead reads{" "}
          {state.run?.workspace ?? "the workspace"} and proposes plans you queue. It never edits
          code.
        </p>
        <div className="new-lead-instead">
          <span>One agent can do it alone?</span>
          <button type="button" onClick={onTaskInstead}>
            <Plus size={15} aria-hidden="true" />
            Start a task instead
          </button>
        </div>
        <div className="new-lead-box">
          <label htmlFor={`${id}-task`}>First task</label>
          <textarea
            id={`${id}-task`}
            ref={inputRef}
            value={draft}
            readOnly={pending || !onStart}
            onChange={(event) => {
              leadDraftStore.getState().setDraft(event.target.value);
            }}
          />
          <div className="new-lead-actions">
            <ChatMergeChip state={state} onOpenSettings={onOpenSettings} />
            <button type="button" className="new-lead-cancel" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="new-lead-start"
              aria-disabled={!canStart}
              onClick={() => {
                if (canStart && onStart)
                  void leadDraftStore
                    .getState()
                    .start(onStart)
                    .then((started) => {
                      if (started) onClose();
                    });
              }}
            >
              Start lead
            </button>
          </div>
        </div>
        {atCap && (
          <p role="status">
            {active.toLocaleString("en-US")} of {max?.toLocaleString("en-US")} leads active. A lead
            must end before another can start.
          </p>
        )}
        {!onStart && <p className="new-lead-replay">Replay: leads cannot be started here.</p>}
        {pending && <p role="status">Starting the lead.</p>}
        {failure && (
          <p role="alert" className="new-lead-failure">
            <X size={16} aria-hidden="true" />
            {failure}
          </p>
        )}
        <LeadLimits state={state} />
      </div>
    </div>
  );
}
