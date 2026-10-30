import { useState } from "react";
import { Inbox } from "lucide-react";
import type { DecisionInboxEntry, State } from "../state";
import { selectDecisionInbox, selectActiveGrants } from "../state";
import { ChangeCard, eventTime } from "./ChangeCard";
import { StandingGrants } from "./StandingGrants";
import { Example } from "./AnswerChoices";
import { selectChangeCard, selectInboxSummary, selectPossibleCallersWait } from "./inboxSelectors";
import { GrantChoice } from "./GrantChoice";
import "./inbox.css";

function InboxRow({
  state,
  entry,
  onReview,
}: {
  state: State;
  entry: DecisionInboxEntry;
  onReview?: (id: string) => void;
}) {
  const change = entry.changeId ? state.changes[entry.changeId] : undefined;
  const wait = entry.changeId ? selectPossibleCallersWait(state, entry.changeId) : null;
  const facts = change?.proposal
    ? state.changeFacts[change.change_id]?.findLast(
        (f) =>
          f.revision === change.proposal?.revision && f.head_commit === change.proposal.head_commit,
      )
    : undefined;
  const question = entry.decisions.find((d) => d.kind === "review_card");
  const followMain = entry.decisions.find((d) => d.kind === "follow_main");
  const example =
    question?.example.source === "review_card"
      ? question.example.value
      : facts?.behavior_diff?.flatMap((b) => b.rows).find((r) => r.before !== r.after);
  const slot = change ? state.agents[change.agent_id]?.slot : entry.decisions[0]?.slot;
  return (
    <article className="inbox-row">
      <div className="decision-heading">
        <span className="decision-muted">
          {slot ? `Agent ${slot}` : "Decision"} · {eventTime(entry.ts)}
        </span>
        <span className="decision-muted">{entry.age}</span>
      </div>
      <h3>
        {followMain
          ? followMain.reason
          : wait
            ? "Possible callers need your decision"
            : question
              ? "Choices need you"
              : (entry.decisions.find((d) => d.kind !== "change")?.reason ??
                change?.proposal?.task ??
                "Decision needs you")}
      </h3>
      <p>{change?.proposal?.summary ?? entry.decisions[0]?.reason}</p>
      {example && <Example row={example} slot={slot ?? undefined} />}
      <InboxAnswer state={state} entry={entry} />
      {change && onReview && (
        <button
          className="review-link"
          onClick={() => {
            onReview(change.change_id);
          }}
        >
          Review change →
        </button>
      )}
    </article>
  );
}

function InboxAnswer({ state, entry }: { state: State; entry: DecisionInboxEntry }) {
  const change = entry.changeId ? state.changes[entry.changeId] : undefined;
  const wait = entry.changeId ? selectPossibleCallersWait(state, entry.changeId) : null;
  const question = entry.decisions.find((d) => d.kind === "review_card");
  const card = change ? selectChangeCard(state, change.change_id) : null;
  if (entry.decisions.some((d) => d.kind === "follow_main")) return null;
  return (
    <>
      {wait ? (
        <div className="possible-wait">
          <div>
            <p>
              <strong>{wait.callers.length} possible callers, not followed</strong>
            </p>
            <ul>
              {wait.callers.map((caller) => (
                <li key={caller.id}>
                  <code>{caller.qualname}</code>
                  <span>not followed</span>
                </li>
              ))}
            </ul>
          </div>
          {!question && (
            <GrantChoice
              key={`${state.run?.run_id ?? ""}:${String(change?.proposal?.revision ?? "")}:${change?.proposal?.head_commit ?? ""}`}
              state={state}
              changeId={change?.change_id ?? ""}
            />
          )}
          {question && card && (
            <details className="inbox-answer">
              <summary>Answer this change before approving</summary>
              <ChangeCard state={state} changeId={change?.change_id ?? ""} />
            </details>
          )}
        </div>
      ) : (
        <>
          {!question && !change && (
            <ul className="decision-reasons">
              {entry.decisions
                .filter((d) => d.kind !== "change")
                .map((d) => (
                  <li key={d.id}>{d.reason}</li>
                ))}
            </ul>
          )}
          {change && card && (
            <details className="inbox-answer">
              <summary>Answer this change</summary>
              <ChangeCard
                key={`${state.run?.run_id ?? ""}:${change.proposal?.head_commit ?? ""}`}
                state={state}
                changeId={change.change_id}
              />
            </details>
          )}
        </>
      )}
    </>
  );
}

export function DecisionInbox({
  state,
  onReview,
}: {
  state: State;
  onReview?: (id: string) => void;
}) {
  const [view, setView] = useState<"decisions" | "grants">("decisions");
  const entries = selectDecisionInbox(state);
  const grants = selectActiveGrants(state);
  const { agents, steps, minutes } = selectInboxSummary(state);
  return (
    <section aria-label="Decisions inbox" className="decisions-inbox">
      <nav aria-label="Inbox views" className="inbox-views">
        <button
          type="button"
          aria-pressed={view === "decisions"}
          onClick={() => {
            setView("decisions");
          }}
        >
          Decisions <span>{entries.length}</span>
        </button>
        <button
          type="button"
          aria-pressed={view === "grants"}
          onClick={() => {
            setView("grants");
          }}
        >
          Standing grants <span>{grants.length}</span>
        </button>
      </nav>
      {view === "grants" ? (
        <StandingGrants state={state} />
      ) : (
        <>
          <header className="inbox-heading">
            <span className="inbox-count">{entries.length}</span>
            <div>
              <h2>{entries.length ? "Decisions waiting for you" : "No decisions waiting"}</h2>
              <p className="decision-muted">
                {agents} agents took {steps} steps
                {minutes === null ? "" : ` in ${String(minutes)} min`}.{" "}
                {entries.length
                  ? `These ${String(entries.length)} need a person.`
                  : "Nothing needs your decision now."}
              </p>
            </div>
          </header>
          <div className="inbox-entries">
            {entries.length ? (
              entries.map((entry) => (
                <InboxRow key={entry.id} state={state} entry={entry} onReview={onReview} />
              ))
            ) : (
              <div className="inbox-empty">
                <Inbox aria-hidden="true" size={28} />
                <h3>You can keep watching</h3>
                <p className="decision-muted">
                  Questions and unavailable checks appear here when they need you. The graph and
                  agent cards keep showing the run.
                </p>
              </div>
            )}
          </div>
          <footer className="inbox-footnote">
            A Yes is prepared, never saved alone. Approve remains a separate decision.
          </footer>
        </>
      )}
    </section>
  );
}
