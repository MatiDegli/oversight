import { Check, X, ArrowRight, LockKeyhole } from "lucide-react";
import { useAnswers } from "../../review";
import { candidateKey, currentCards } from "../../review/answers";
import { approvalLocked, approvalLockedCaption } from "../../review/ReviewDecisionBar";
import { caseSlot } from "./phoneEvidence";
import { PhoneDiff } from "./PhoneDiff";
import { PhoneReview } from "./PhoneReview";
import type { PanelProps } from "./PracticePanel";
import "./phoneRows.css";

export function PhoneDecision({ practice, attempt, ...actions }: PanelProps) {
  const entry = practice.cases[attempt.index];
  if (!entry) return null;
  return (
    <>
      <main className="pp-main">
        <div className="pp-progress">
          <span>
            Decision {attempt.index + 1} of {practice.cases.length}
          </span>
          <span className="pp-ticks" aria-hidden="true">
            {practice.cases.map((item, i) => (
              <span key={item.changeId} className={i <= attempt.index ? "is-active" : ""} />
            ))}
          </span>
        </div>
        <PhoneReview
          state={practice.cut}
          changeId={entry.changeId}
          title={entry.title}
          slot={caseSlot(practice, entry)}
          diff={<PhoneDiff practice={practice} entry={entry} />}
        />
      </main>
      <DecisionDock practice={practice} attempt={attempt} {...actions} />
    </>
  );
}

export function DecisionDock({ practice, attempt, ...actions }: PanelProps) {
  const entry = practice.cases[attempt.index];
  const answer = entry && attempt.answers[entry.changeId];
  const changeId = entry?.changeId ?? "";
  const answers = useAnswers((s) => s.drafts[candidateKey(practice.cut, changeId)]) ?? {};
  const locked = approvalLocked(currentCards(practice.cut, changeId), answers);
  return (
    <footer className={`pp-dock ${answer ? "is-answered" : ""}`}>
      {!answer && locked && <p className="pp-lock-caption">{approvalLockedCaption}</p>}
      {answer ? (
        <>
          <div className="pp-saved" role="status">
            <Check size={20} />
            <div>
              <span>Your choice</span>
              <strong>{answer}</strong>
            </div>
          </div>
          <button
            className="pp-button pp-primary"
            onClick={attempt.index === 0 ? actions.onNext : actions.onReveal}
          >
            {attempt.index === 0 ? "Next" : "Show findings"}
            <ArrowRight size={18} />
          </button>
        </>
      ) : (
        <>
          <button
            className="pp-button pp-choice"
            disabled={locked}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") event.preventDefault();
            }}
            onClick={(event) => {
              if (event.detail !== 0) actions.onAnswer("Approve");
            }}
          >
            {locked ? <LockKeyhole size={18} /> : <Check size={18} />}
            Approve
          </button>
          <button
            className="pp-button pp-choice"
            onClick={() => {
              actions.onAnswer("Reject");
            }}
          >
            <X size={18} />
            Reject
          </button>
        </>
      )}
    </footer>
  );
}
