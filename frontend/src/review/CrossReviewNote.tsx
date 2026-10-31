import { MessageSquare } from "lucide-react";

import type { ChangeState } from "../state";

const verdicts: Record<NonNullable<ChangeState["crossReview"]>["verdict"], string> = {
  clean: "No findings",
  changes_requested: "Changes requested",
  unknown: "Review unavailable",
};

const reasons: Record<string, string> = {
  round_exhausted:
    "The automatic revision round was used. Review the change and decide how to continue.",
  reviewer_missing: "A reviewer is not configured. Review the change yourself.",
  reviewer_same: "The reviewer must use a different model. Review the change yourself.",
  reviewer_credentials:
    "The reviewer is not available. Check its configuration or review the change yourself.",
  author_halted: "The author stopped before finishing. Review the change before continuing.",
  ledger: "Review accounting failed. Stop the server and settle usage before another run.",
  spend_invalid: "The spending brake stopped review. Check usage before another run.",
  main_moved: "Main changed. Review the combined result before merging.",
};

/** Advisory text only: no focus, commands, raw diagnostics or filesystem data. */
export function CrossReviewNote({
  change,
  agentNote = false,
  reviewerLabel,
}: {
  change: ChangeState;
  agentNote?: boolean;
  reviewerLabel?: string;
}) {
  if (change.personActed) return null;
  const { crossReview: review, crossReviewRound: round, needsYou: note } = change;
  if (!review && !round && !note) return null;
  if (agentNote && review) {
    const blocking = review.findings.filter((f) => f.severity === "BLOCKING").length;
    const notes = review.findings.length - blocking;
    const model =
      reviewerLabel ??
      (review.reviewer_model === "meta-models/muse-spark-open-1026"
        ? "Muse Spark Open"
        : review.reviewer_model);
    return (
      <section aria-label="Cross-review" className="review-cross-note">
        <p>{model} reviewed this change</p>
        <p>
          {!review.complete ||
          review.verdict === "unknown" ||
          review.findings.some((f) => f.severity === null)
            ? "Review unavailable"
            : blocking || review.verdict === "changes_requested"
              ? "Changes requested"
              : `Nothing blocking · ${String(notes)} note${notes === 1 ? "" : "s"}`}
        </p>
        {review.findings.map((finding, i) => (
          <p key={i}>
            {finding.path}
            {finding.line ? `:${String(finding.line)}` : ""}
            {finding.path ? " · " : ""}
            {finding.text}
          </p>
        ))}
      </section>
    );
  }
  return (
    <section
      aria-label="Cross-review"
      className="shrink-0 border-b border-page-border px-4.5 py-2.5 text-sm text-muted"
    >
      <p className="flex items-center gap-2 font-semibold text-text">
        <MessageSquare aria-hidden="true" className="size-4 shrink-0" /> Cross-review
      </p>
      {review && (
        <p>
          {review.reviewer_model} reviewed {review.author_model}: {verdicts[review.verdict]}.
          {review.findings.length > 0 && ` ${String(review.findings.length)} findings.`}
          {!review.complete && " Review incomplete."}
        </p>
      )}
      {round && <p>One automatic Request changes round was used.</p>}
      {note && (
        <p>
          Needs you:{" "}
          {reasons[note.code] ??
            "Automatic review could not continue. Review the change and decide how to continue."}
        </p>
      )}
      {!note && <p>Advisory review. You decide what reaches main.</p>}
    </section>
  );
}
