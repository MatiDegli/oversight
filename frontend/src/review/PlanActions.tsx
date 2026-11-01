import { useId, useState } from "react";
import { Clock, X } from "lucide-react";
import type { Plan, QueuedTask } from "@contracts";
import type { State } from "../state";
import { replayDecisionWords, useDecisions } from "./DecisionContext";
import {
  dependencyWords,
  firstWaveSize,
  planAttemptPending,
  planFits,
  selectPlanQueue,
} from "./planSelectors";
import "./planActions.css";

export default function PlanActions({
  state,
  plan,
  slots,
}: {
  state: State;
  plan: Plan;
  slots: { held: number; total: number } | null;
}) {
  const { planActions, replayOf } = useDecisions();
  const queue = selectPlanQueue(state, plan)?.payload;
  const wave = firstWaveSize(plan);
  const waiting =
    queue?.detail ??
    (queue?.state === "queued" && slots
      ? `Needs ${String(wave)} free ${wave === 1 ? "slot" : "slots"}; ${String(slots.held)} of ${String(slots.total)} slots are held.`
      : queue?.state === "queued"
        ? "Waiting for slot evidence."
        : null);
  return (
    <div className="plan-content">
      {plan.queued_task_id && (
        <p className="plan-status">
          {queue?.state === "failed" ? (
            <X size={13} aria-hidden="true" />
          ) : (
            <Clock size={13} aria-hidden="true" />
          )}
          {queue
            ? queue.state.charAt(0).toUpperCase() + queue.state.slice(1)
            : "Queue link recorded"}
        </p>
      )}
      {plan.subtasks && (
        <div className="plan-piece">
          <header>
            <strong>Plan</strong>
            <span>Drafted by the lead, tracked by Oversight</span>
            <span className="plan-count">{plan.subtasks.length} subtasks</span>
          </header>
          <ol>
            {plan.subtasks.map((task, index) => (
              <li key={index}>
                <span className="plan-circle" aria-hidden="true" />
                <div>
                  <p>
                    <span className="plan-number">{index + 1}</span>
                    {task.task}
                  </p>
                  <p className="plan-condition">{dependencyWords(task)}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
      {plan.state === "failed" ? (
        <blockquote>
          <X size={14} aria-hidden="true" />
          {plan.detail}
        </blockquote>
      ) : (
        <>
          <p className="plan-explanation">
            {plan.queued_task_id && queue?.state === "queued" && (
              <Clock size={13} aria-hidden="true" />
            )}
            {plan.queued_task_id
              ? queue?.state === "queued" || queue?.state === "blocked"
                ? waiting
                : queue?.detail
              : `${String(wave)} ${wave === 1 ? "subtask starts" : "subtasks start"} first${wave < (plan.subtasks?.length ?? 0) ? "; the rest start after a prerequisite merges" : ""}.`}
          </p>
          {planActions ? (
            <PlanControls state={state} plan={plan} queue={queue} />
          ) : (
            <p className="plan-explanation">{replayDecisionWords(replayOf)}</p>
          )}
        </>
      )}
    </div>
  );
}

function PlanControls({ state, plan, queue }: { state: State; plan: Plan; queue?: QueuedTask }) {
  const { planActions } = useDecisions();
  const [rejecting, setRejecting] = useState(false);
  const [comment, setComment] = useState("");
  const label = useId();
  const attempt = planActions?.attempts[plan.plan_id];
  const pending = planAttemptPending(state, plan, queue, attempt);
  const failure =
    attempt?.run === state.run &&
    attempt.phase === "failed" &&
    planAttemptPending(state, plan, queue, { ...attempt, phase: "pending" })
      ? attempt.message
      : undefined;
  const undispatched =
    plan.state === "proposed" &&
    (!plan.queued_task_id || queue?.state === "queued" || queue?.state === "blocked");
  const fits = planFits(plan, state.run?.slots.length ?? 0);
  const send = (action: "queue" | "reject" | "cancel") =>
    planActions?.send(plan, action, queue, comment);
  return (
    <>
      {undispatched && (
        <div className="plan-controls">
          {rejecting ? (
            <>
              <label htmlFor={label}>Comment, saved with the plan (optional)</label>
              <input
                id={label}
                value={comment}
                disabled={pending}
                onChange={(event) => {
                  setComment(event.target.value);
                }}
              />
              <button
                className="plan-reject plan-outlined"
                disabled={pending}
                onClick={() => send("reject")}
              >
                <X size={14} aria-hidden="true" />
                Reject plan
              </button>
              <button
                disabled={pending}
                className="plan-outlined"
                onClick={() => {
                  setRejecting(false);
                }}
              >
                Keep plan
              </button>
            </>
          ) : (
            <>
              {plan.queued_task_id ? (
                <button className="plan-outlined" disabled={pending} onClick={() => send("cancel")}>
                  Take out of the queue
                </button>
              ) : (
                <button
                  className="plan-queue"
                  disabled={pending || !fits}
                  onClick={() => send("queue")}
                >
                  Queue plan
                </button>
              )}
              <button
                className="plan-reject"
                disabled={pending}
                onClick={() => {
                  setRejecting(true);
                }}
              >
                <X size={14} aria-hidden="true" />
                Reject plan
              </button>
            </>
          )}
        </div>
      )}
      {!plan.queued_task_id && !fits && (
        <p className="plan-explanation">
          The first wave exceeds the {state.run?.slots.length ?? 0} offered slots.
        </p>
      )}
      {pending && (
        <p className="plan-explanation" role="status">
          Waiting for the server’s{" "}
          {attempt?.action === "cancel"
            ? "queue cancellation"
            : attempt?.action === "reject"
              ? "plan rejection"
              : "queue"}{" "}
          evidence.
        </p>
      )}
      {failure && (
        <p className="plan-explanation" role="alert">
          <X size={14} aria-hidden="true" />
          {failure}
        </p>
      )}
    </>
  );
}
