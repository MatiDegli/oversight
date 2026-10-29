import { useState } from "react";
import { Check, GitMerge, LockKeyhole, X } from "lucide-react";
import { ChangeFactsRow } from "./ChangeFactsRow";
import { EditorDiff } from "../editor";
import { grantReasons, grantScopeWords, selectLiveDiff } from "../state";
import type { State } from "../state";
import { agentSlotStyle } from "../theme";
import { AnswerChoices, cardKey } from "./AnswerChoices";
import type { Answers } from "./AnswerChoices";
import { selectChangeCard, selectUnchanged } from "./inboxSelectors";
import "./inbox.css";

export const eventTime = (ts: string) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "America/Argentina/Buenos_Aires",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(ts)) + " ART";

/** Stage-one controls prepare local view state; they never send commands or forge events. */
function LocalDecisions({
  cards,
  answers,
}: {
  cards: Parameters<typeof AnswerChoices>[0]["cards"];
  answers: Answers;
}) {
  const values = cards.map((card) => answers[cardKey(card)] ?? card.answer);
  const no = cards.find((_card, i) => values[i] === "no");
  const prefill = no?.example
    ? `${no.question}\n${no.example.inputs}: ${no.example.before} → ${no.example.after}`
    : no?.question;
  const commentKey = no ? cardKey(no) : "general";
  const [comments, setComments] = useState<Record<string, string>>({});
  const comment = comments[commentKey] ?? prefill ?? "";
  const [notice, setNotice] = useState("");
  const answered = values.filter((v) => v === "yes" || v === "no").length;
  const allYes = values.every((v) => v === "yes");
  return (
    <footer className="local-decisions">
      <label>
        Comment for the agent
        <textarea
          aria-label="Comment for the agent"
          value={comment}
          onChange={(e) => {
            setComments((old) => ({ ...old, [commentKey]: e.target.value }));
          }}
          rows={2}
        />
      </label>
      <div className="decision-heading">
        <p>
          <strong>
            {cards.length
              ? `${String(answered)} of ${String(cards.length)} answered`
              : "The check is still unavailable"}
          </strong>
          <br />
          <span className="decision-muted">
            {cards.length
              ? `${values.includes("unsure") ? "Unsure waits here. " : ""}Approve opens when every answer is Yes.`
              : "You decide whether to continue without it."}
          </span>
        </p>
        <div className="decision-actions">
          {no ? (
            <button
              className="decision-primary"
              onClick={() => {
                setNotice("Request changes prepared locally. No command sent.");
              }}
            >
              Request changes
            </button>
          ) : (
            <button
              className="decision-primary"
              disabled={!allYes}
              onClick={() => {
                setNotice("Approve prepared locally. No command sent.");
              }}
            >
              {!allYes && <LockKeyhole size={14} aria-hidden="true" />} Approve and merge
            </button>
          )}
          {!no && (
            <button
              disabled={!comment.trim()}
              onClick={() => {
                setNotice("Request changes prepared locally. No command sent.");
              }}
            >
              Request changes
            </button>
          )}
          <button
            className="decision-reject"
            onClick={() => {
              setNotice("Reject prepared locally. No command sent.");
            }}
          >
            <X size={15} aria-hidden="true" />
            Reject
          </button>
        </div>
      </div>
      <p role="status" className="decision-muted">
        {notice || "Preview · answers stay in this view; decisions are not sent."}
      </p>
    </footer>
  );
}

export function ChangeCard({ state, changeId }: { state: State; changeId: string }) {
  const card = selectChangeCard(state, changeId);
  const candidate = JSON.stringify([
    state.run?.run_id,
    changeId,
    card?.change.proposal?.revision,
    card?.change.proposal?.head_commit,
  ]);
  const [draft, setDraft] = useState<{ candidate: string; answers: Answers }>();
  const answers = draft?.candidate === candidate ? draft.answers : {};
  if (!card) return null;
  const { change, facts, kind } = card;
  const slot = state.agents[change.agent_id]?.slot;
  const questions = facts?.review_cards ?? [];
  const unchanged = selectUnchanged(facts);
  const grantUses = change.resolution?.grants ?? [];
  return (
    <aside aria-label="Change card" className="decision-card" style={agentSlotStyle(slot)}>
      <header className="decision-card-header">
        <p className="decision-muted">
          <span className="decision-agent">{slot}</span> Agent {slot}{" "}
          <span>
            ·{" "}
            {state.run?.models.find((model) => model.id === state.agents[change.agent_id]?.model)
              ?.label ?? state.agents[change.agent_id]?.model}
          </span>
        </p>
        <h2>
          {kind === "merged"
            ? "Merged by rules"
            : kind === "unavailable"
              ? "Needs you because a check was unavailable"
              : questions.length
                ? `${String(questions.length)} choices need you`
                : "Needs you for a choice"}
        </h2>
        <p>Asked: {change.proposal?.task}</p>
        <p>Done: {change.proposal?.summary}</p>
        <ChangeFactsRow state={state} changeId={changeId} />
      </header>
      <div className="decision-card-body">
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
            cards={questions}
            answers={answers}
            onAnswer={(key, value) => {
              setDraft((old) => ({
                candidate,
                answers: { ...(old?.candidate === candidate ? old.answers : {}), [key]: value },
              }));
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
        {kind !== "unavailable" && (
          <section className="unchanged-facts">
            <h3>
              <Check size={16} aria-hidden="true" />
              {unchanged.incomplete ? "What was compared" : "Everything else compared is the same"}
            </h3>
            <p className="decision-muted">
              {unchanged.unchanged} of {unchanged.compared} compared inputs returned the same
              result.{unchanged.incomplete && " Comparison coverage is incomplete."} This says
              nothing about inputs that were not compared.
            </p>
          </section>
        )}
        {Object.keys(change.files).map((path) => {
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
      </div>
      {kind !== "merged" && <LocalDecisions cards={questions} answers={answers} />}
    </aside>
  );
}
