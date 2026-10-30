import { replayDecisionWords, useDecisions } from "./DecisionContext";
import { useId, useState } from "react";
import { LockKeyhole, X } from "lucide-react";
import type { ReviewCard } from "@contracts";
import type { Answers } from "./AnswerChoices";
import { cardAnswer, cardKey } from "./AnswerChoices";
import type { DecisionAction, DecisionView } from "./decisions";

export function DecisionFeedback({ view }: { view: DecisionView }) {
  const { replayOf } = useDecisions();
  const failure = view.kind === "open" || view.kind === "conflict" ? view.failure : null;
  return (
    <>
      <p role="status" className="decision-muted">
        {view.kind === "pending"
          ? view.action === "approve"
            ? "Approving: waiting for the merge result."
            : view.action === "reject"
              ? "Rejecting: waiting for the server."
              : "Requesting changes: waiting for the agent."
          : view.kind === "recorded"
            ? replayDecisionWords(replayOf)
            : view.kind === "revising"
              ? "The agent is revising. Decisions reopen when it proposes again."
              : ""}
      </p>
      {failure && <p role="alert">{failure}</p>}
    </>
  );
}
export function CardDecisions({
  cards,
  answers,
  candidate,
  view,
  onDecide,
}: {
  cards: ReviewCard[];
  answers: Answers;
  candidate: string;
  view: DecisionView;
  onDecide?: (action: DecisionAction, comment: string) => void;
}) {
  const values = cards.map((card) => cardAnswer(card, answers));
  const approvalReason = useId();
  const no = cards.find((_card, i) => values[i] === "no");
  const prefill = no?.example
    ? `${no.question}\n${no.example.inputs}: ${no.example.before} → ${no.example.after}`
    : no?.question;
  const commentKey = candidate + (no ? cardKey(no) : "general");
  const [comments, setComments] = useState<Record<string, string>>({});
  const comment = comments[commentKey] ?? prefill ?? "";
  const answered = values.filter((v) => v != null).length;
  const allYes = values.every((v) => v === "yes");
  const open = !!onDecide && (view.kind === "open" || view.kind === "conflict");
  return (
    <footer className="local-decisions">
      <label>
        Comment for the agent
        <textarea
          aria-label="Comment for the agent"
          value={comment}
          readOnly={!open}
          onChange={(e) => {
            setComments((old) => ({ ...old, [commentKey]: e.target.value }));
          }}
          rows={2}
        />
      </label>
      <div className="decision-heading">
        <p id={approvalReason} aria-live="polite" aria-atomic="true">
          <strong>
            {cards.length
              ? `${String(answered)} of ${String(cards.length)} answered`
              : "The check is still unavailable"}
          </strong>
          <br />
          <span className="decision-muted">
            {cards.length
              ? `${values.includes("unsure") ? "Unsure waits here. " : ""}Approve opens only when every answer is Yes`
              : "You decide whether to continue without it."}
          </span>
        </p>
        <CardActions
          no={!!no}
          open={open}
          allYes={allYes}
          view={view}
          approvalReason={approvalReason}
          comment={comment}
          onDecide={onDecide}
        />
      </div>
      <DecisionFeedback view={view} />
    </footer>
  );
}

function CardActions({
  no,
  open,
  allYes,
  view,
  approvalReason,
  comment,
  onDecide,
}: {
  no: boolean;
  open: boolean;
  allYes: boolean;
  view: DecisionView;
  approvalReason: string;
  comment: string;
  onDecide?: (action: DecisionAction, comment: string) => void;
}) {
  return (
    <div className="decision-actions">
      {!no && (
        <button
          className="decision-primary"
          disabled={!open || !allYes || view.kind !== "open"}
          aria-describedby={approvalReason}
          onClick={(event) => {
            if (event.detail > 0) onDecide?.("approve", comment);
          }}
        >
          {!allYes && <LockKeyhole size={14} aria-hidden="true" />} Approve and merge
        </button>
      )}
      <button
        className={no ? "decision-primary" : undefined}
        disabled={!open || !comment.trim()}
        onClick={() => onDecide?.("request-changes", comment)}
      >
        Request changes
      </button>
      <button
        className="decision-reject"
        disabled={!open}
        onClick={() => onDecide?.("reject", comment)}
      >
        <X size={15} aria-hidden="true" />
        Reject
      </button>
    </div>
  );
}
