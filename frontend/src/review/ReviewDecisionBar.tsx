import type { ChangeResolved, ReviewCard } from "@contracts";
import { Check, GitMerge, LockKeyhole, MessageSquare, X } from "lucide-react";
import { useId, useState } from "react";
import type { Answers } from "./AnswerChoices";
import { cardAnswer, cardKey } from "./AnswerChoices";
import type { DecisionView } from "./decisions";
import type { ReviewPanelProps } from "./ReviewPanel";
import "./inbox.css";
import { approvalLocked, approvalLockedCaption } from "./answers";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
/** The recorded or returned decision, in words, with an icon so color is never alone. */
function Resolution({ resolution }: { resolution: ChangeResolved }) {
  let ok = false;
  let text: string;
  if (resolution.decision === "approved" && resolution.merge === "merged") {
    ok = true;
    text = "Approved and merged into main";
  } else if (resolution.decision === "approved" && resolution.conflict?.kind === "tests") {
    text = "Approved, not merged: the tests failed on the combined result with main";
  } else if (resolution.decision === "approved") {
    const paths = resolution.conflict?.paths.join(", ");
    text = `Approved, not merged: the merge conflicts with main${paths ? ` in ${paths}` : ""}`;
  } else if (resolution.decision === "rejected") {
    text =
      resolution.reason === "stopped"
        ? "The agent was stopped and its change discarded"
        : "Rejected: the change was discarded";
  } else {
    text = "Changes requested: the comment went back to the agent";
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="flex items-center gap-2">
        {ok ? (
          <Check aria-hidden="true" className="size-4 shrink-0 text-passed" />
        ) : resolution.decision === "changes_requested" ? (
          <MessageSquare aria-hidden="true" className="size-4 shrink-0 text-muted" />
        ) : (
          <X aria-hidden="true" className="size-4 shrink-0 text-failed-text" />
        )}
        <span>{text}</span>
        {resolution.merge_commit && (
          <span className="font-mono text-muted" title={resolution.merge_commit}>
            {resolution.merge_commit.slice(0, 7)}
          </span>
        )}
      </p>
      {resolution.comment && <p className="text-sm text-muted">Comment: {resolution.comment}</p>}
    </div>
  );
}

const button = `inline-flex h-10 items-center gap-2 rounded-[7px] px-4 text-sm font-semibold disabled:cursor-not-allowed disabled:text-faint ${focusRing}`;
const primary = `${button} bg-text text-page hover:bg-text-hover disabled:bg-page-raised disabled:ring-1 disabled:ring-inset disabled:ring-page-border`;
const destructive = `${button} text-failed-text hover:bg-raised disabled:hover:bg-transparent`;
const bar = "review-decision-bar shrink-0 border-t border-divider bg-page";

export function ReviewDecisionBar({
  changeId,
  view,
  onDecide,
  replayOf,
  blocked = null,
  cards = [],
  answers = {},
  candidate = changeId,
  unavailable = false,
}: {
  changeId: string;
  view: DecisionView;
  onDecide?: ReviewPanelProps["onDecide"];
  replayOf?: string;
  blocked?: "loading" | "edits" | null;
  cards?: ReviewCard[];
  answers?: Answers;
  candidate?: string;
  unavailable?: boolean;
}) {
  const approvalReason = useId();
  const values = cards.map((card) => cardAnswer(card, answers));
  const no = cards.find((_card, i) => values[i] === "no");
  const prefill = no?.example
    ? `${no.question}\n${no.example.inputs}: ${no.example.before} → ${no.example.after}`
    : no?.question;
  const commentKey = candidate + (no ? cardKey(no) : "general");
  const [comments, setComments] = useState<Record<string, string>>({});
  const comment = comments[commentKey] ?? prefill ?? "";
  const locked = approvalLocked(cards, answers);
  if (view.kind === "decided") {
    return (
      <section aria-label="Decision" className={bar}>
        <Resolution resolution={view.resolution} />
      </section>
    );
  }
  const recorded = view.kind === "recorded";
  // What the log is comes from its source: only a real recording may call itself one.
  const replay = replayOf === undefined ? "Replay" : `Replay of a ${replayOf}`;
  if (recorded && view.resolution) {
    return (
      <section aria-label="Decision" className={bar}>
        <p className="text-sm text-muted">{`${replay}: this is the decision the log holds.`}</p>
        <Resolution resolution={view.resolution} />
      </section>
    );
  }
  const hasCaption = locked || unavailable || (!recorded && view.kind !== "conflict");
  const pending = view.kind === "pending";
  const revising = view.kind === "revising";
  const approvable = view.kind === "open" && onDecide !== undefined && !blocked && !locked;
  const requestable =
    (view.kind === "open" || view.kind === "conflict") && onDecide !== undefined && !blocked;
  const rejectable = (view.kind === "open" || view.kind === "conflict") && onDecide !== undefined;
  const failure = view.kind === "open" || view.kind === "conflict" ? view.failure : null;
  // No key in this section makes a decision. Approve answers only a pointer click: Enter and Space
  // on the focused button arrive as a click with detail 0, which AGENTS.md forbids it to act on.
  // Reject stays keyboard-operable, and Escape in the comment box only leaves the box.
  return (
    <section aria-label="Decision" className={bar}>
      {view.kind === "conflict" && <Resolution resolution={view.resolution} />}
      {recorded && (
        <p className="text-sm text-muted">
          {`${replay}: nothing here sends a decision. The decision in the log shows when it arrives.`}
        </p>
      )}
      {(!recorded || cards.length > 0 || unavailable) && (
        <textarea
          aria-label="Comment for the agent"
          value={comment}
          rows={2}
          readOnly={recorded || pending || revising}
          onChange={(event) => {
            setComments((old) => ({ ...old, [commentKey]: event.target.value }));
          }}
          onKeyDown={(event) => {
            // The app's Escape closes review, which would drop the comment; here it only leaves.
            if (event.key === "Escape" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.blur();
            }
          }}
          placeholder="Comment for the agent"
          className={`block h-16 w-full resize-none rounded-[10px] border border-control-edge bg-field px-3.5 py-2.5 text-[15px] text-text placeholder:text-faint read-only:text-muted ${focusRing}`}
        />
      )}
      <div className="review-decision-actions mt-2.5 flex flex-wrap items-center gap-2.5">
        <button
          type="button"
          disabled={!approvable}
          aria-describedby={hasCaption ? approvalReason : undefined}
          onClick={(event) => {
            // Keyboard and scripted clicks have detail 0; only a pointer can merge.
            if (event.detail === 0) return;
            onDecide?.(changeId, "approve", comment);
          }}
          className={primary}
        >
          {locked ? (
            <LockKeyhole aria-hidden="true" className="size-4" />
          ) : (
            <GitMerge aria-hidden="true" className="size-4" />
          )}{" "}
          Approve and merge
        </button>
        <button
          type="button"
          disabled={!requestable || comment.trim() === ""}
          onClick={() => {
            if (requestable) onDecide(changeId, "request-changes", comment);
          }}
          aria-description="Request changes keeps the worktree and sends your comment back."
          className={`${button} border border-control-edge`}
        >
          <MessageSquare aria-hidden="true" className="size-4" /> Request changes
        </button>
        <button
          type="button"
          disabled={!rejectable}
          onClick={() => {
            onDecide?.(changeId, "reject", comment);
          }}
          className={`ml-auto ${destructive}`}
        >
          <X aria-hidden="true" className="size-4" /> Reject
        </button>
      </div>
      {hasCaption && (
        <p
          id={approvalReason}
          className="review-decision-caption mt-2 text-[13px] leading-[18px] text-muted"
        >
          {locked
            ? approvalLockedCaption
            : unavailable
              ? "The check is still unavailable. You decide whether to continue without it."
              : "If main moved since the agent started, the tests run again on the combined result before the merge."}
        </p>
      )}
      {revising &&
        (view.resolution ? (
          <Resolution resolution={view.resolution} />
        ) : (
          <p role="status">The agent is revising. Decisions reopen when it proposes again.</p>
        ))}
      {blocked === "edits" && (
        <p role="status" className="text-sm text-muted">
          Save or discard your review edits before approving or requesting changes.
        </p>
      )}
      {/* Always mounted, so what appears under the buttons is announced where it appears. */}
      <div role="status" className="text-sm text-muted">
        {pending && (
          <p className="mt-2.5">
            {view.action === "request-changes"
              ? "Requesting changes: waiting for the agent."
              : view.action === "approve"
                ? "Approving: waiting for the merge result."
                : "Rejecting: waiting for the server."}
          </p>
        )}
      </div>
      {failure !== null && (
        <p role="alert" className="flex items-start gap-2 text-sm text-text">
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" /> {failure}
        </p>
      )}
    </section>
  );
}
