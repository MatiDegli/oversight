import { useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { freezePractice } from "./session";
import { replayBuild, replaySource, replayRecording } from "../../transport";
import { viewReducer } from "../view";
import type { View } from "../view";
import { manifest, validateManifest } from "./manifest";
import { answerOnce, complete, freshAttempt, PracticePanel } from "./PracticePanel";
import type { Attempt } from "./PracticePanel";

function availablePractice() {
  if (!replayBuild || replaySource.kind !== "real recorded run") return null;
  try {
    return validateManifest(manifest, replayRecording);
  } catch {
    return null;
  }
}
const practice = availablePractice();

export function usePractice(
  setView: Dispatch<SetStateAction<View>>,
  stop: () => void,
  restart: () => void,
) {
  const [active, setActive] = useState(false);
  const [attempt, setAttempt] = useState<Attempt>(freshAttempt);
  if (!practice) return undefined;
  const bound = practice;
  function show(index: number) {
    const item = bound.cases[index];
    if (!item) return;
    setView((view) =>
      viewReducer(view, {
        type: "open",
        tab: {
          id: "practice",
          selected: item.proposal.graph.nodes_modified[0] ?? null,
          review: item.changeId,
          diff: null,
          file: null,
          frameIds: item.proposal.graph.nodes_modified,
          frameKey: `practice:${item.changeId}`,
        },
      }),
    );
  }
  return {
    active,
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
