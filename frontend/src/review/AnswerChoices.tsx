import type { BehaviorDiffRow, ReviewCard, Slot } from "@contracts";
import { Fragment } from "react";
import { flushSync } from "react-dom";
import type { BehaviorNodeView } from "./behaviorEvidence";
import { ArrowRight } from "lucide-react";
import { agentSlotStyle } from "../theme";

export type Answers = Record<string, ReviewCard["answer"]>;
export const cardKey = (card: ReviewCard) =>
  JSON.stringify([card.node_id, card.question, card.evidence_seq]);
export const cardAnswer = (card: ReviewCard, answers: Answers) =>
  Object.hasOwn(answers, cardKey(card)) ? (answers[cardKey(card)] ?? null) : card.answer;
const labels = {
  yes: "Yes, keep it",
  no: "No, send it back",
  unsure: "Unsure, decide later",
} satisfies Record<NonNullable<ReviewCard["answer"]>, string>;

export function Example({ row, slot }: { row: BehaviorDiffRow; slot?: Slot }) {
  if (row.before === row.after) return null;
  return (
    <div className="choice-example" style={agentSlotStyle(slot)}>
      <code>{row.inputs}</code>
      <div className="example-pair">
        <div>
          <span>Before · main</span>
          <code>{row.before}</code>
        </div>
        <ArrowRight aria-hidden="true" size={18} />
        <div className="example-after">
          <span>After · Agent {slot ?? "?"}</span>
          <code>{row.after}</code>
        </div>
      </div>
      {row.unstable && <p>Results varied between executions; this example is unstable.</p>}
    </div>
  );
}

type ChoiceProps = {
  card: ReviewCard;
  index: number;
  answers: Answers;
  onAnswer: (key: string, answer: ReviewCard["answer"]) => void;
  slot?: Slot;
  panelComparisons?: BehaviorNodeView[];
};
function AnswerChoice({ card, index, answers, onAnswer, slot, panelComparisons }: ChoiceProps) {
  const key = cardKey(card);
  const answer = cardAnswer(card, answers);
  const comparison = panelComparisons?.find((b) => b.node_id === card.node_id);
  const Controls = panelComparisons ? "div" : Fragment;
  return (
    <section className="choice-question" key={key} aria-label={card.question}>
      <div className="decision-heading">
        <h3>
          <span className="question-number">{index + 1}.</span>{" "}
          {answer && !panelComparisons ? `${card.question} · ${labels[answer]}` : card.question}
          {answer &&
            !panelComparisons &&
            card.example &&
            card.example.before !== card.example.after && (
              <code className="collapsed-example">
                {" "}
                {card.example.inputs}: {card.example.before} → {card.example.after}
              </code>
            )}
        </h3>
        {answer && (
          <Controls {...(panelComparisons ? { className: "answered-controls" } : {})}>
            {panelComparisons && <span>{labels[answer]}</span>}
            <button
              aria-label={panelComparisons ? "Change answer" : undefined}
              className="answer-reset"
              onClick={(event) => {
                answerQuestion(event.currentTarget, key, null, onAnswer);
              }}
            >
              {panelComparisons ? "Change" : "Change answer"}
            </button>
          </Controls>
        )}
      </div>
      {answer && panelComparisons && card.example && card.example.before !== card.example.after && (
        <code className="collapsed-example">
          {card.example.inputs} · {card.example.before} → {card.example.after}
        </code>
      )}
      {!answer && (
        <>
          {comparison && (
            <p className="choice-caption">
              {comparison.label} · from {comparison.compared_count} compared{" "}
              {comparison.compared_count === 1 ? "input" : "inputs"}
            </p>
          )}
          {card.example ? (
            <Example row={card.example} slot={slot} />
          ) : (
            <p className="decision-muted">No example was recorded for this question.</p>
          )}
          <Controls {...(panelComparisons ? { className: "panel-choice-actions" } : {})}>
            {panelComparisons && <p className="choice-prompt">Keep this behavior?</p>}
            <div className="decision-actions">
              {(["yes", "no", "unsure"] as const).map((value) => (
                <button
                  type="button"
                  key={value}
                  onClick={(event) => {
                    answerQuestion(event.currentTarget, key, value, onAnswer);
                  }}
                >
                  {labels[value]}
                </button>
              ))}
            </div>
          </Controls>
        </>
      )}
    </section>
  );
}
export function AnswerChoices({
  cards,
  ...props
}: Omit<ChoiceProps, "card" | "index"> & { cards: ReviewCard[] }) {
  return (
    <div>
      {cards.map((card, index) => (
        <AnswerChoice key={cardKey(card)} card={card} index={index} {...props} />
      ))}
    </div>
  );
}

function answerQuestion(
  button: HTMLButtonElement,
  key: string,
  answer: ReviewCard["answer"],
  onAnswer: ChoiceProps["onAnswer"],
) {
  const section = button.closest("section");
  const hadFocus = document.activeElement === button;
  flushSync(() => {
    onAnswer(key, answer);
  });
  // Keep focus within the question when the person's action replaces its controls.
  if (hadFocus)
    section
      ?.querySelector<HTMLButtonElement>(answer ? ".answer-reset" : ".decision-actions button")
      ?.focus({ preventScroll: true });
}
