import { AgentBadge } from "../teams/badge";
import { presentationFor } from "../teams/presentation";
import { lazy, Suspense, useState } from "react";
import { Inbox } from "lucide-react";
import { slotUsage } from "../agents";
import type { Plan } from "@contracts";
import type { DecisionInboxEntry, State } from "../state";
import { selectDecisionInbox, selectActiveGrants } from "../state";
import { ChangeCard, eventTime } from "./ChangeCard";
import { InboxViews, InboxHeading } from "./InboxHeader";
import { StandingGrants } from "./StandingGrants";
import { Example } from "./AnswerChoices";
import { selectChangeCard, selectInboxSummary, selectPossibleCallersWait } from "./inboxSelectors";
import { factsForRevision } from "./factsForRevision";
import { GrantChoice } from "./GrantChoice";
import { selectPlanEvidence, selectRetainedPlans } from "./planSelectors";
import "./inbox.css";

const PlanActions = lazy(() => import("./PlanActions"));
type OpenWorkflow = (id: string, launcher: HTMLButtonElement) => void;
const planPiece = (
  state: State,
  plan: Plan,
  onReview?: (id: string) => void,
  onOpenWorkflow?: OpenWorkflow,
) => (
  <Suspense
    fallback={
      <div className="plan-content">
        <p className="decision-muted">Loading plan…</p>
      </div>
    }
  >
    <PlanActions
      key={`${state.run?.run_id ?? ""}:${plan.plan_id}`}
      state={state}
      plan={plan}
      slots={slotUsage(state)}
      onReview={onReview}
      onOpenWorkflow={onOpenWorkflow}
    />
  </Suspense>
);

function InboxRow({
  state,
  entry,
  plan,
  onReview,
  onOpenWorkflow,
}: {
  state: State;
  entry: DecisionInboxEntry;
  plan: Plan | undefined;
  onReview?: (id: string) => void;
  onOpenWorkflow?: OpenWorkflow;
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
    ? (plan.detail ?? (plan.subtasks ? null : "Lead plan awaits approval"))
    : (change?.proposal?.summary ?? entry.decisions[0]?.reason);
  const reasons = [
    ...new Set(entry.decisions.filter((d) => d.kind !== "change").map((d) => d.reason)),
  ].filter(
    (reason) =>
      reason !== heading &&
      reason !== paragraph &&
      !(plan?.subtasks && reason === "Lead plan awaits approval"),
  );
  const example =
    question?.example.source === "review_card"
      ? question.example.value
      : facts?.behavior_diff?.flatMap((b) => b.rows).find((r) => r.before !== r.after);
  const identity = presentationFor(state, change?.agent_id ?? entry.decisions[0]?.agentId);
  return (
    <article className="inbox-row">
      <div className="decision-heading">
        <span className="decision-muted">
          {identity ? <AgentBadge identity={identity} /> : "Decision"} ·{" "}
          <time>{eventTime(entry.ts)}</time>
        </span>
        <span className="decision-muted">{entry.age}</span>
      </div>
      <h3>{heading}</h3>
      {paragraph && paragraph !== heading && <p>{paragraph}</p>}
      {!question && !change && reasons.length > 0 && (
        <ul className="decision-reasons">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {plan?.subtasks && planPiece(state, plan, onReview, onOpenWorkflow)}
      {example && <Example row={example} identity={identity} />}
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
  onOpenWorkflow,
}: {
  state: State;
  onReview?: (id: string) => void;
  onOpenWorkflow?: OpenWorkflow;
}) {
  const [view, setView] = useState<"decisions" | "grants">("decisions");
  const entries = selectDecisionInbox(state);
  const grants = selectActiveGrants(state);
  const { agents, steps, minutes } = selectInboxSummary(state);
  const { plans } = selectPlanEvidence(state);
  const retained = selectRetainedPlans(state);
  return (
    <section aria-label="Decisions inbox" className="decisions-inbox">
      <InboxViews count={entries.length} grants={grants.length} view={view} onView={setView} />
      {view === "grants" ? (
        <StandingGrants state={state} />
      ) : (
        <>
          <InboxHeading count={entries.length} agents={agents} steps={steps} minutes={minutes} />
          <div className="inbox-entries">
            {entries.length ? (
              entries.map((entry) => {
                const lead = entry.decisions.find((d) => d.kind === "lead_plan");
                return (
                  <InboxRow
                    key={entry.id}
                    state={state}
                    entry={entry}
                    plan={lead?.planId ? plans.get(lead.planId)?.payload : undefined}
                    onReview={onReview}
                    onOpenWorkflow={onOpenWorkflow}
                  />
                );
              })
            ) : retained.queued.length ||
              retained.running.length ||
              retained.failed.length ? null : (
              <div className="inbox-empty">
                <Inbox aria-hidden="true" size={28} />
                <h3>You can keep watching</h3>
                <p className="decision-muted">
                  Questions and unavailable checks appear here when they need you. The graph and
                  agent cards keep showing the run.
                </p>
              </div>
            )}
            {(["queued", "running", "failed"] as const).map(
              (group) =>
                retained[group].length > 0 && (
                  <section
                    className="plan-group"
                    aria-label={
                      group === "queued"
                        ? "Queued, waiting for slots"
                        : group === "running"
                          ? "Running plans"
                          : "Ended without running"
                    }
                    key={group}
                  >
                    <h3 className="group-label">
                      {group === "queued"
                        ? "Queued, waiting for slots"
                        : group === "running"
                          ? "Running plans"
                          : "Ended without running"}
                    </h3>
                    {retained[group].map(({ payload: plan, firstTs }) => (
                      <article className="inbox-row" key={plan.plan_id}>
                        <div className="decision-heading">
                          <span className="decision-muted">
                            Plan · <time>{eventTime(firstTs)}</time>
                          </span>
                        </div>
                        <h3>{plan.task}</h3>
                        {group === "queued" && plan.detail && plan.detail !== plan.task && (
                          <p>{plan.detail}</p>
                        )}
                        {planPiece(state, plan, onReview, onOpenWorkflow)}
                      </article>
                    ))}
                  </section>
                ),
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
