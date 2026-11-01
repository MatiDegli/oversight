import type { EvidencePractice as Practice } from "./caseEvidence";
export type Answer = "Approve" | "Reject";
export type Attempt = {
  index: number;
  started: boolean;
  revealed: boolean;
  finished?: boolean;
  answers: Partial<Record<string, Answer>>;
};
export const freshAttempt = (): Attempt => ({
  index: 0,
  started: false,
  revealed: false,
  answers: {},
});
export function answerOnce(attempt: Attempt, id: string, answer: Answer): Attempt {
  return attempt.answers[id]
    ? attempt
    : { ...attempt, answers: { ...attempt.answers, [id]: answer } };
}
export const complete = (practice: Practice, attempt: Attempt) =>
  practice.cases.every((item) => attempt.answers[item.changeId]);
export const score = (practice: Practice, attempt: Attempt) =>
  practice.cases.filter((item) => attempt.answers[item.changeId] === item.reference).length;
