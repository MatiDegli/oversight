import { GitMerge, LockKeyhole } from "lucide-react";
import { ReviewHeader } from "./ReviewHeader";
import { EditorDiff } from "../editor";
import { grantReasons, grantScopeWords, selectLiveDiff } from "../state";
import type { State } from "../state";
import { agentSlotStyle } from "../theme";
import { AnswerChoices, cardAnswer } from "./AnswerChoices";
import { candidateKey, useAnswers } from "./answers";
import { useDecisions } from "./DecisionContext";
import { ReviewDecisionBar } from "./ReviewDecisionBar";
import { CardDecisions } from "./CardDecisions";
import { decisionView } from "./decisions";
import type { ReviewPanelProps } from "./ReviewPanel";
import { GrantChoice } from "./GrantChoice";
import { selectChangeCard, selectPossibleCallersWait } from "./inboxSelectors";
import type { ChangeCardState } from "./inboxSelectors";
import { ComparisonMap } from "./ComparisonMap";
import "./inbox.css";

export const eventTime = (ts: string) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "America/Argentina/Buenos_Aires",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(ts)) + " ART";

export function ChangeCard({
  state,
  changeId,
  attempt,
  onDecide,
  onClose,
  card: projectedCard,
  reviewPanel = false,
  replayOf,
}: Pick<ReviewPanelProps, "state" | "changeId" | "attempt" | "onDecide" | "replayOf"> & {
  onClose?: () => void;
  card?: ChangeCardState;
  reviewPanel?: boolean;
}) {
  const shared = useDecisions();
  const decide = onDecide ?? shared.onDecide;
  const card = projectedCard ?? selectChangeCard(state, changeId);
  const candidate = candidateKey(state, changeId);
  const answers = useAnswers((s) => s.drafts[candidate]) ?? {};
  if (!card) return null;
  const { change, facts, kind } = card;
  const slot = state.agents[change.agent_id]?.slot;
  const Decisions = reviewPanel ? PanelDecisions : CardDecisions;
  return (
    <aside aria-label="Change card" className="decision-card" style={agentSlotStyle(slot)}>
      <ReviewHeader
        state={state}
        changeId={changeId}
        status={kind === "merged" ? "Merged" : undefined}
        onClose={onClose}
      />
      <div className="decision-card-body">
        <CardLead state={state} changeId={changeId} card={card} />
        {reviewPanel && !!facts?.review_cards?.length && (
          <p className="decision-muted">
            {facts.review_cards.filter((card) => cardAnswer(card, answers) != null).length} of{" "}
            {facts.review_cards.length} answered
          </p>
        )}
        <CardEvidence state={state} changeId={changeId} card={card} />
        {kind !== "unavailable" && <ComparisonMap state={state} change={change} facts={facts} />}
        <CardDiffs state={state} changeId={changeId} />
      </div>
      {kind !== "merged" && selectPossibleCallersWait(state, changeId) && (
        <GrantChoice state={state} changeId={changeId} scopeOnly />
      )}
      {kind !== "merged" && (
        <Decisions
          changeId={changeId}
          replayOf={replayOf ?? shared.replayOf}
          unavailable={kind === "unavailable"}
          candidate={candidate}
          cards={facts?.review_cards ?? []}
          answers={answers}
          view={decisionView(change, state.errors, attempt ?? shared.attempts?.[changeId], !decide)}
          onDecide={
            decide
              ? (action, comment) => {
                  decide(changeId, action, comment);
                }
              : undefined
          }
        />
      )}
    </aside>
  );
}

type CardContentProps = { state: State; changeId: string; card: ChangeCardState };

function CardLead({ card }: CardContentProps) {
  const { change, kind } = card;
  const questions = card.facts?.review_cards ?? [];
  return (
    <div>
      <h2>
        {kind === "merged"
          ? "Merged by rules"
          : kind === "unavailable"
            ? "Needs you because a check was unavailable"
            : questions.length
              ? `${String(questions.length)} choices need you`
              : "Needs you for a choice"}
      </h2>
      <p>
        Asked: {change.proposal?.task} · Done: {change.proposal?.summary}
      </p>
    </div>
  );
}

function CardDiffs({ state, changeId }: { state: State; changeId: string }) {
  const change = state.changes[changeId];
  return (
    <>
      {change &&
        Object.keys(change.files).map((path) => {
          const data = selectLiveDiff(state, changeId, path);
          return (
            data && (
              <details className="card-diff" key={path}>
                <summary>
                  Show diff · <code>{path}</code>
                </summary>
                <div>
                  <EditorDiff data={data} />
                </div>
              </details>
            )
          );
        })}
    </>
  );
}

export function CardEvidence({ state, changeId, card }: CardContentProps) {
  const candidate = candidateKey(state, changeId);
  const answers = useAnswers((s) => s.drafts[candidate]) ?? {};
  const { change, facts, kind } = card;
  const slot = state.agents[change.agent_id]?.slot;
  const grantUses = change.resolution?.grants ?? [];
  return (
    <>
      {kind === "merged" ? (
        <>
          <p className="merged-summary">
            <GitMerge size={18} aria-hidden="true" />
            Merged into main · <code>{change.resolution?.merge_commit}</code>
          </p>
          {grantUses.map((use) => (
            <p className="grant-receipt" key={use.grant_id}>
              Under grant <code>{use.grant_id}</code> · {grantReasons[use.authorization.reason]} ·{" "}
              {grantScopeWords(use.authorization.scope)} · given{" "}
              {eventTime(use.authorization.creation_ts)}.
            </p>
          ))}
        </>
      ) : kind === "choice" ? (
        <AnswerChoices
          cards={facts?.review_cards ?? []}
          answers={answers}
          onAnswer={(key, value) => {
            useAnswers.getState().answer(candidate, key, value);
          }}
          slot={slot}
        />
      ) : (
        <div className="unavailable-check">
          <LockKeyhole size={20} aria-hidden="true" />
          <h3>{change.needsYou?.reason}</h3>
          {facts?.problems.map((p) => (
            <p key={p}>{p}</p>
          ))}
          <p>
            A missing check is not a passing check. Review the diff, request changes, or reject.
          </p>
        </div>
      )}
    </>
  );
}

function PanelDecisions({
  onDecide,
  ...props
}: Parameters<typeof CardDecisions>[0] & {
  changeId: string;
  replayOf?: string;
  unavailable?: boolean;
}) {
  return (
    <ReviewDecisionBar
      {...props}
      onDecide={
        onDecide
          ? (_id, action, comment) => {
              onDecide(action, comment);
            }
          : undefined
      }
    />
  );
}
