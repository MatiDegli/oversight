import { X } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { selectAgentCard } from "../agents";
import { costText } from "../state";
import type { ChangeState, State } from "../state";
import { presentationFor, presentationStyle } from "../teams/presentation";
import { ChangeFactsRow } from "./ChangeFactsRow";
import { ChangeTiming } from "./StageTimes";
import "./reviewHeader.css";

const statusWords: Record<ChangeState["status"], string> = {
  editing: "Editing",
  ready_for_review: "Ready for review",
  revising: "Revising",
  conflict: "Not merged",
  approved: "Approved",
  rejected: "Rejected",
};

/** Past this many characters a task likely needs more than two lines; measuring then decides. */
export const longTask = 140;

/**
 * The task as headline, clamped to two lines so the diff keeps its room. The clamp is CSS only:
 * the whole text stays in the DOM for screen readers. The toggle appears only when the text overflows.
 */
export function TaskHeading({ task }: { task: string }) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(task.length > longTask);
  useLayoutEffect(() => {
    const element = heading.current;
    // Expanded text has no clamp to overflow; the toggle stays so the person can clamp it again.
    if (!element || expanded || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      setOverflows(element.scrollHeight > element.clientHeight + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [task, expanded]);
  return (
    <div className="review-header-task min-w-0">
      <h2
        ref={heading}
        title={task}
        className={`min-w-0 wrap-anywhere text-xl leading-7 font-bold ${expanded ? "" : "line-clamp-2"}`}
      >
        {task}
      </h2>
      {overflows && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => {
            setExpanded((value) => !value);
          }}
          className="text-[13px] leading-5 font-semibold text-muted underline hover:text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
        >
          {expanded ? "Show less" : "Show full task"}
        </button>
      )}
    </div>
  );
}

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
  const identity = presentationFor(state, change?.agent_id);
  const base = change?.proposal?.base_commit || agent?.base_commit;
  const task = change?.proposal?.task ?? agent?.task ?? "Change";
  return (
    <header inert={inert} className="review-header" style={presentationStyle(identity)}>
      <div className="flex items-center gap-3">
        {identity && (
          <span role="img" aria-label={`Agent ${identity.label}`} className="review-header-badge">
            {identity.label}
          </span>
        )}
        <TaskHeading key={changeId} task={task} />
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
        {identity ? `Agent ${identity.label}` : "Agent unknown"} ·{" "}
        {card?.modelLabel ?? "Model unknown"} ·{" "}
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
      {change && (
        <ChangeTiming
          stages={state.changeStages[changeId] ?? null}
          rounds={change.revisionRounds}
        />
      )}
      <div className="mt-3">
        <ChangeFactsRow state={state} changeId={changeId} />
      </div>
    </header>
  );
}
