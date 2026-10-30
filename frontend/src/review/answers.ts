import type { GrantRequest, ReviewCard } from "@contracts";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import type { State } from "../state";
import { cardAnswer, cardKey, type Answers } from "./AnswerChoices";
import { factsForRevision } from "./factsForRevision";

export const candidateKey = (state: State, id: string) => {
  const p = state.changes[id]?.proposal;
  return JSON.stringify([state.run?.run_id, id, p?.revision, p?.head_commit]);
};
const empty: Answers = {};
const answerStore = createStore<{
  drafts: Record<string, Answers>;
  grants: Record<string, GrantRequest | undefined>;
  answer: (candidate: string, key: string, value: ReviewCard["answer"]) => void;
  grant: (candidate: string, value?: GrantRequest) => void;
}>((set) => ({
  drafts: {},
  grants: {},
  answer: (candidate, key, value) => {
    set((s) => ({
      drafts: {
        ...s.drafts,
        [candidate]: { ...s.drafts[candidate], [key]: value },
      },
    }));
  },
  grant: (candidate, value) => {
    set((s) => ({ grants: { ...s.grants, [candidate]: value } }));
  },
}));
export const useAnswers = Object.assign(function useAnswers<T>(
  selector: (state: ReturnType<typeof answerStore.getState>) => T,
) {
  return useStore(answerStore, selector);
}, answerStore);
export const answersFor = (candidate: string) => useAnswers.getState().drafts[candidate] ?? empty;
export const currentCards = (state: State, id: string) => {
  const p = state.changes[id]?.proposal;
  return p ? (factsForRevision(state, p)?.review_cards ?? []) : [];
};
export function answeredCards(state: State, id: string) {
  const answers = answersFor(candidateKey(state, id));
  return currentCards(state, id).flatMap((card) => {
    const answer = cardAnswer(card, answers);
    return answer ? [{ ...card, answer, answered_by: "person" as const }] : [];
  });
}
export { cardKey };
