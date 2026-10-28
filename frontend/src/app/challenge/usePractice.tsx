import { useMemo, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { freezePractice } from "./session";
import { replayRecording } from "../../transport";
import type { ChosenReplaySource } from "../../transport";
import { viewReducer } from "../view";
import type { View, FocusTab } from "../view";
import { answerOnce, complete, freshAttempt, PracticePanel } from "./PracticePanel";
import type { Attempt } from "./PracticePanel";

import { availablePractice } from "./availability";

export function usePractice(
  setView: Dispatch<SetStateAction<View>>,
  stop: () => void,
  restart: () => void,
  replayBuild: boolean,
  replaySource: ChosenReplaySource,
) {
  const practice = useMemo(
    () => availablePractice(replayBuild, replaySource, replayRecording),
    [replayBuild, replaySource],
  );
  const [active, setActive] = useState(false);
  const [attempt, setAttempt] = useState<Attempt>(freshAttempt);
  if (!practice) return undefined;
  const bound = practice;
  function focus(index: number): FocusTab {
    const item = bound.cases[index] ?? bound.cases[0];
    if (!item) throw new Error("Practice has no cases");
    return {
      id: "practice",
      selected: item.proposal.graph.nodes_modified[0] ?? null,
      review: item.changeId,
      diff: null,
      file: null,
      frameIds: item.proposal.graph.nodes_modified,
      frameKey: `practice:${item.changeId}`,
    };
  }
  function show(index: number) {
    setView((view) => viewReducer(view, { type: "open", tab: focus(index) }));
  }
  return {
    active,
    focus: focus(attempt.index),
    open() {
      if (!active) {
        freezePractice(stop, bound.cut, document.getElementById("root"));
        setActive(true);
      }
      show(attempt.index);
    },
    content: (
      <PracticePanel
        practice={bound}
        attempt={attempt}
        onStart={() => {
          setAttempt((current) => ({ ...current, started: true }));
        }}
        onAnswer={(answer) => {
          setAttempt((current) => {
            const item = bound.cases[current.index];
            return item ? answerOnce(current, item.changeId, answer) : current;
          });
        }}
        onNext={() => {
          if (bound.cases[0] && attempt.answers[bound.cases[0].changeId]) {
            setAttempt((current) => ({ ...current, index: 1 }));
            show(1);
          }
        }}
        onReveal={() => {
          if (complete(bound, attempt)) setAttempt((current) => ({ ...current, revealed: true }));
        }}
        onRestart={() => {
          setAttempt(freshAttempt());
          show(0);
        }}
        onExit={() => {
          setActive(false);
          setAttempt(freshAttempt());
          setView((view) => viewReducer(view, { type: "watch" }));
          restart();
        }}
      />
    ),
  };
}
