import type { BehaviorDiffRow, ReviewCard, Slot } from "@contracts";
import { flushSync } from "react-dom";
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

export function AnswerChoices({
  cards,
  answers,
  onAnswer,
  slot,
}: {
  cards: ReviewCard[];
  answers: Answers;
  onAnswer: (key: string, answer: ReviewCard["answer"]) => void;
  slot?: Slot;
}) {
  const answerQuestion = (button: HTMLButtonElement, key: string, answer: ReviewCard["answer"]) => {
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
  };
  return (
    <div>
      {cards.map((card, index) => {
        const key = cardKey(card);
        const answer = cardAnswer(card, answers);
        return (
          <section className="choice-question" key={key} aria-label={card.question}>
            <div className="decision-heading">
              <h3>
                <span className="question-number">{index + 1}.</span>{" "}
                {answer
                  ? labels[answer]
                  : card.question === "Was this behavior change asked for?"
                    ? "The task did not ask for this difference. Keep it?"
                    : card.question}
                {answer && card.example && card.example.before !== card.example.after && (
                  <code className="collapsed-example">
                    {" "}
                    {card.example.inputs}: {card.example.before} → {card.example.after}
                  </code>
                )}
              </h3>
              {answer && (
                <button
                  className="answer-reset"
                  onClick={(event) => {
                    answerQuestion(event.currentTarget, key, null);
                  }}
                >
                  Change answer
                </button>
              )}
            </div>
            {!answer && (
              <>
                {card.example ? (
                  <Example row={card.example} slot={slot} />
                ) : (
                  <p className="decision-muted">No example was recorded for this question.</p>
                )}
                <div className="decision-actions">
                  {(["yes", "no", "unsure"] as const).map((value) => (
                    <button
                      type="button"
                      key={value}
                      onClick={(event) => {
                        answerQuestion(event.currentTarget, key, value);
                      }}
                    >
                      {labels[value]}
                    </button>
                  ))}
                </div>
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}
