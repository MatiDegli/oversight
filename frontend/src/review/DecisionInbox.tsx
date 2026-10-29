import { useId, useState } from "react";
import { Check, ChevronDown, Inbox } from "lucide-react";
import type { DecisionInboxEntry, State } from "../state";
import { selectDecisionInbox } from "../state";
import { ChangeCard, eventTime } from "./ChangeCard";
import { Example } from "./AnswerChoices";
import { selectInboxSummary, selectPossibleCallersWait } from "./inboxSelectors";
import "./inbox.css";

function GrantChoice({ module }: { module?: string }) {
  const scopeId = useId();
  const [scope, setScope] = useState<"module" | "run" | null>(null);
  const [notice, setNotice] = useState("");
  return (
    <div className="grant-choice">
      <button
        className="decision-primary"
        onClick={() => {
          setNotice(`Approve prepared${scope ? ` with ${scope} grant` : ""}. No command sent.`);
        }}
      >
        Approve
      </button>
      <details
        onToggle={(event) => {
          if (!event.currentTarget.open) setScope(null);
        }}
      >
        <summary>
          Don't ask again <ChevronDown size={14} aria-hidden="true" />
        </summary>
        <fieldset>
          <legend>Allow possible callers, not followed</legend>
          <label>
            <input
              type="radio"
              name={scopeId}
              checked={scope === "module"}
              disabled={!module}
              onChange={() => {
                setScope("module");
              }}
            />
            This module <code>{module ?? "Multiple files; module scope unavailable"}</code>
          </label>
          <label>
            <input
              type="radio"
              name={scopeId}
              checked={scope === "run"}
              onChange={() => {
                setScope("run");
              }}
            />
            This run
          </label>
          <p>Ends with this run. Created only when you Approve.</p>
        </fieldset>
      </details>
      <p role="status" className="decision-muted">
        {notice}
      </p>
    </div>
  );
}

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
        {wait
          ? "Possible callers need your decision"
          : question
            ? "Choices outside the task"
            : (entry.decisions.find((d) => d.kind !== "change")?.reason ??
              change?.proposal?.task ??
              "Decision needs you")}
      </h3>
      <p>{change?.proposal?.summary ?? entry.decisions[0]?.reason}</p>
      {example && <Example row={example} slot={slot ?? undefined} />}
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
          {!question && <GrantChoice module={wait.module} />}
          {question && (
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
          {change && (
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

export function DecisionInbox({
  state,
  onReview,
}: {
  state: State;
  onReview?: (id: string) => void;
}) {
  const entries = selectDecisionInbox(state);
  const { agents, steps, minutes } = selectInboxSummary(state);
  return (
    <section aria-label="Decisions inbox" className="decisions-inbox">
      <header className="inbox-heading">
        <span className="inbox-count">{entries.length}</span>
        <div>
          <h2>{entries.length ? "Decisions waiting for you" : "No decisions waiting"}</h2>
          <p className="decision-muted">
            {agents} agents took {steps} steps{minutes === null ? "" : ` in ${String(minutes)} min`}
            .{" "}
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
              Questions and unavailable checks appear here when they need you. The graph and agent
              cards keep showing the run.
            </p>
          </div>
        )}
      </div>
      <footer className="inbox-footnote">
        <Check size={16} aria-hidden="true" /> A Yes is prepared, never saved alone. Approve remains
        a separate decision.
      </footer>
    </section>
  );
}
