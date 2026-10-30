import { CircleAlert } from "lucide-react";
import { Fragment } from "react";
import type { ReactNode } from "react";
import type { State } from "../state";
import { grantReasons, grantScopeWords } from "../state";
import { cardAnswer, Example } from "./AnswerChoices";
import { candidateKey, useAnswers } from "./answers";
import { CardEvidence, eventTime } from "./ChangeCard";
import { CrossReviewNote } from "./CrossReviewNote";
import { ReviewFacts } from "./ReviewFacts";
import {
  nothingComparedLine,
  partialLines,
  selectReviewBody,
  smallestExample,
} from "./reviewBodyEvidence";
import "./reviewBody.css";

/** One evidence order, with one parent-owned scroll; event updates never replace that parent. */
export function ReviewBody({
  state,
  changeId,
  diff,
  showCrossReview,
}: {
  state: State;
  changeId: string;
  diff: ReactNode;
  showCrossReview: boolean;
}) {
  const { kind, card, facts, callers, summary } = selectReviewBody(state, changeId);
  const change = state.changes[changeId];
  const slot = change ? state.agents[change.agent_id]?.slot : undefined;
  const answers = useAnswers((s) => s.drafts[candidateKey(state, changeId)]) ?? {};
  const questions = facts?.review_cards ?? [];
  const title =
    kind === "a"
      ? questions.length === 1
        ? "Needs you for a choice"
        : `${String(questions.length)} choices need you`
      : kind === "b"
        ? `${String(callers.differing.length)} caller${callers.differing.length === 1 ? " behaves" : "s behave"} differently`
        : kind === "c"
          ? "Partial evidence"
          : kind === "d2"
            ? nothingComparedLine(state, changeId)
            : kind === "d"
              ? "Nothing was compared for this change."
              : kind === "g"
                ? "Merged by your rules"
                : kind === "unavailable"
                  ? "Needs you because a check was unavailable"
                  : null;
  const mode = change?.resolution?.assessment?.mode ?? state.run?.merge_mode;
  const caption =
    kind === "a"
      ? `${String(questions.filter((q) => cardAnswer(q, answers) !== null).length)} of ${String(questions.length)} answered`
      : kind === "b"
        ? `${String(callers.measured)} of ${String(callers.total)} callers measured`
        : kind === "c"
          ? "Read the diff before approving"
          : kind === "d2"
            ? "The diff and tests are the evidence."
            : kind === "d"
              ? "Behavior and callers were not recorded for this revision. The diff and tests are the evidence."
              : kind === "g" && mode === "merge_when_safe"
                ? "Merge mode: Merge alone when safe"
                : kind === "unavailable"
                  ? `Asked: ${change?.proposal?.task ?? ""} · Done: ${change?.proposal?.summary ?? ""}`
                  : null;
  const comparison = ["a", "b", "c", "g", "other"].includes(kind) && (
    <section aria-label="What was compared" className="review-compared">
      <h3>What was compared</h3>
      <div className="comparison-lines">
        {summary.map((line) => (
          <p key={line}>
            {line
              .split(/(\d+)/)
              .map((part, i) => (/^\d+$/.test(part) ? <strong key={i}>{part}</strong> : part))}
          </p>
        ))}
      </div>
      <p className="comparison-bound">
        Only recorded inputs were compared; others can still differ.
      </p>
      {change?.proposal && (
        <ReviewFacts state={state} proposal={change.proposal} scrollable={false} panel />
      )}
    </section>
  );
  return (
    <section aria-label="Review context" className={`review-body review-body-${kind}`}>
      {title && (
        <section aria-label="Review lead" className="review-lead">
          <div className="review-lead-heading">
            {kind === "d" || kind === "d2" ? (
              <p className="review-lone">{title}</p>
            ) : (
              <h3>{title}</h3>
            )}
            {caption && <p>{caption}</p>}
          </div>
          {kind === "g" &&
            change?.resolution?.grants?.map((use) => (
              <p className="grant-receipt" key={use.grant_id}>
                Under grant <code>{use.grant_id}</code> · {grantReasons[use.authorization.reason]} ·{" "}
                {grantScopeWords(use.authorization.scope)} · given{" "}
                {eventTime(use.authorization.creation_ts)}.
              </p>
            ))}
          {card && (kind === "a" || kind === "unavailable") && (
            <CardEvidence state={state} changeId={changeId} card={card} panel />
          )}
          {kind === "c" && (
            <ul className="partial-lines">
              {partialLines(state, changeId).map((item) => (
                <li key={item.nodeId}>
                  <CircleAlert aria-hidden="true" size={16} />
                  <div>
                    {item.line}
                    {item.example && <Example row={item.example} slot={slot} />}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {kind === "b" &&
            callers.differing.map((row) => {
              const e = row.evidence;
              const example = e ? smallestExample(e.rows) : undefined;
              return (
                <div className="differing-caller" key={row.nodeId}>
                  <p>
                    <span className="caller-distance">{row.distance}</span>
                    <code>{row.name}</code>
                    <span>
                      {e?.differing_count} of {e?.compared_count} recorded calls changed
                    </span>
                  </p>
                  {example && <Example row={example} slot={slot} />}
                </div>
              );
            })}
          {kind === "b" &&
            callers.same.map((line) => (
              <p className="same-callers" key={line}>
                {line}
              </p>
            ))}
        </section>
      )}
      <section aria-label="Agent note" className="review-agent-note">
        <p>Agent {slot ?? "?"} says</p>
        <p>{change?.proposal?.summary ?? "The agent left no note."}</p>
        {change && showCrossReview && (
          <CrossReviewNote
            change={change}
            agentNote
            reviewerLabel={
              state.run?.models.find((m) => m.id === change.crossReview?.reviewer_model)?.label
            }
          />
        )}
      </section>
      {(kind === "a" || kind === "g" ? ["comparison", "diff"] : ["diff", "comparison"]).map(
        (part) => (
          <Fragment key={part}>{part === "diff" ? diff : comparison}</Fragment>
        ),
      )}
    </section>
  );
}
