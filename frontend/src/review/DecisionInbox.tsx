import { useState } from "react";
import { Inbox } from "lucide-react";
import type { Plan } from "@contracts";
import type { DecisionInboxEntry, State } from "../state";
import { selectDecisionInbox, selectActiveGrants } from "../state";
import { ChangeCard, eventTime } from "./ChangeCard";
import { StandingGrants } from "./StandingGrants";
import { Example } from "./AnswerChoices";
import { selectChangeCard, selectInboxSummary, selectPossibleCallersWait } from "./inboxSelectors";
import { factsForRevision } from "./factsForRevision";
import { GrantChoice } from "./GrantChoice";
import "./inbox.css";

function InboxRow({
  state,
  entry,
  plan,
  onReview,
}: {
  state: State;
  entry: DecisionInboxEntry;
  plan: Plan | undefined;
  onReview?: (id: string) => void;
}) {
  const change = entry.changeId ? state.changes[entry.changeId] : undefined;
  const wait = entry.changeId ? selectPossibleCallersWait(state, entry.changeId) : null;
  const facts = change?.proposal ? factsForRevision(state, change.proposal) : undefined;
  const question = entry.decisions.find((d) => d.kind === "review_card");
  const followMain = entry.decisions.find((d) => d.kind === "follow_main");
  const heading =
    plan?.task ??
    (followMain
      ? followMain.reason
      : wait
        ? "Possible callers need your decision"
        : question
          ? "Choices need you"
          : (entry.decisions.find((d) => d.kind !== "change")?.reason ??
            change?.proposal?.task ??
            "Decision needs you"));
  const paragraph = plan
    ? (plan.detail ?? "Lead plan awaits approval")
    : (change?.proposal?.summary ?? entry.decisions[0]?.reason);
  const reasons = [
    ...new Set(entry.decisions.filter((d) => d.kind !== "change").map((d) => d.reason)),
  ].filter((reason) => reason !== heading && reason !== paragraph);
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
      <h3>{heading}</h3>
      {paragraph && paragraph !== heading && <p>{paragraph}</p>}
      {reasons.length > 0 && (
        <ul className="decision-reasons">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
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
  const card = change ? selectChangeCard(state, change.change_id, entry) : null;
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
              <ChangeCard state={state} changeId={change?.change_id ?? ""} card={card} />
            </details>
          )}
        </div>
      ) : (
        <>
          {change && card && (
            <details className="inbox-answer">
              <summary>Answer this change</summary>
              <ChangeCard
                key={`${state.run?.run_id ?? ""}:${change.proposal?.head_commit ?? ""}`}
                state={state}
                changeId={change.change_id}
                card={card}
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
  const plans = new Map<string, Plan>();
  for (const event of state.decisionEvents) {
    if (event.type !== "plan.updated") continue;
    const previous = plans.get(event.payload.plan_id);
    if (!previous || previous.revision <= event.payload.revision)
      plans.set(event.payload.plan_id, event.payload);
  }
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
                {agents} {agents === 1 ? "agent" : "agents"} took {steps}{" "}
                {steps === 1 ? "step" : "steps"}
                {minutes === null ? "" : ` in ${String(minutes)} min`}.{" "}
                {entries.length
                  ? `These ${String(entries.length)} need a person.`
                  : "Nothing needs your decision now."}
              </p>
            </div>
          </header>
          <div className="inbox-entries">
            {entries.length ? (
              entries.map((entry) => {
                const lead = entry.decisions.find((d) => d.kind === "lead_plan");
                return (
                  <InboxRow
                    key={entry.id}
                    state={state}
                    entry={entry}
                    plan={lead?.planId ? plans.get(lead.planId) : undefined}
                    onReview={onReview}
                  />
                );
              })
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
