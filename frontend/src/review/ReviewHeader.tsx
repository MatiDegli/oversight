import { X } from "lucide-react";
import { selectAgentCard } from "../agents";
import { costText } from "../state";
import type { ChangeState, State } from "../state";
import { agentSlotStyle } from "../theme";
import { ChangeFactsRow } from "./ChangeFactsRow";
import "./reviewHeader.css";

const statusWords: Record<ChangeState["status"], string> = {
  editing: "Editing",
  ready_for_review: "Ready for review",
  revising: "Revising",
  conflict: "Not merged",
  approved: "Approved",
  rejected: "Rejected",
};

/** Shared anatomy for ordinary review, choices and recorded resolutions. */
export function ReviewHeader({
  state,
  changeId,
  status,
  onClose,
  inert,
}: {
  state: State;
  changeId: string;
  status?: string;
  onClose?: () => void;
  inert?: boolean;
}) {
  const change = state.changes[changeId];
  const card = change ? selectAgentCard(state, change.agent_id) : undefined;
  const agent = card?.agent;
  const base = change?.proposal?.base_commit || agent?.base_commit;
  const task = change?.proposal?.task ?? agent?.task ?? "Change";
  return (
    <header inert={inert} className="review-header" style={agentSlotStyle(agent?.slot)}>
      <div className="flex items-center gap-3">
        {agent && (
          <span role="img" aria-label={`Agent ${agent.slot}`} className="review-header-badge">
            {agent.slot}
          </span>
        )}
        <h2 title={task} className="min-w-0 wrap-anywhere text-xl leading-7 font-bold">
          {task}
        </h2>
        {change && (
          <span className="review-header-status">{status ?? statusWords[change.status]}</span>
        )}
        {onClose && (
          <button
            type="button"
            aria-label="Close review"
            title="Close (Escape)"
            onClick={onClose}
            className="ml-auto grid size-7 shrink-0 place-items-center rounded-[7px] text-muted hover:bg-raised hover:text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        )}
      </div>
      <p className="mt-1.5 wrap-anywhere text-[13px] leading-5 text-muted">
        {agent ? `Agent ${agent.slot}` : "Agent unknown"} · {card?.modelLabel ?? "Model unknown"} ·{" "}
        {card?.cost == null ? "cost unknown" : costText(card.cost)} ·{" "}
        {card ? `step ${String(card.step)} of ${String(card.maxSteps)}` : "steps unknown"} ·
        worktree{" "}
        <code
          className="font-mono"
          title={change ? `.runtime/worktrees/${change.agent_id}` : undefined}
        >
          {change?.agent_id ?? "unknown"}
        </code>{" "}
        · base{" "}
        <code className="font-mono" title={base ?? undefined}>
          {base?.slice(0, 7) ?? "unknown"}
        </code>
      </p>
      <div className="mt-3">
        <ChangeFactsRow state={state} changeId={changeId} />
      </div>
    </header>
  );
}
