import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";

import type { FileSaved } from "@contracts";

import type { FileDraft } from "../editor";
import { sendDecision } from "../review";
import type { DecisionAction, DecisionAttempt } from "../review";
import { stateStore } from "../state";
import { createCommandClient, replayBuild, replaySource } from "../transport";
import { createFileSession } from "./files";
import { useEscapeCloses } from "./escape";
import { Frame } from "./Frame";
import { startAppSession } from "./session";
import {
  clickNode,
  closePanel,
  initialView,
  nextReview,
  openChange,
  openReview,
  panelOpen,
} from "./view";
import type { View } from "./view";

export function App() {
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(initialView);
  const [attempts, setAttempts] = useState<Record<string, DecisionAttempt>>({});
  // The replay build has no backend: it never sends a command or starts an agent, and review shows
  // recorded decisions.
  const commands = useMemo<ReturnType<typeof createCommandClient> | null>(
    () => (replayBuild ? null : createCommandClient()),
    [],
  );
  const drafts = useRef(new Map<string, FileDraft>());
  const inFlightSaves = useRef(new Map<string, Promise<FileSaved>>());
  // A stable session owns settlement; its getters read these refs only in handlers and effects.
  // The factory stores these getters and never reads either ref during render.
  // eslint-disable-next-line react-hooks/refs
  const [files] = useState(() =>
    commands
      ? createFileSession(
          commands,
          () => drafts.current,
          () => inFlightSaves.current,
        )
      : undefined,
  );
  const state = useStore(stateStore, (store) => store.state);
  useEffect(() => {
    return startAppSession(document.getElementById("root"), (failure) => {
      setError(
        failure === null ? null : failure instanceof Error ? failure.message : "Transport failed",
      );
    });
  }, []);
  // The graph keeps one tap handler; it reads the store when the tap happens.
  const onNodeTap = useCallback(
    (nodeId: string) => {
      setView((current) =>
        clickNode(stateStore.getState().state, current, nodeId, commands !== null),
      );
    },
    [commands],
  );
  const onOpenChange = useCallback((changeId: string) => {
    setView((current) => openChange(stateStore.getState().state, current, changeId) ?? current);
  }, []);
  const onClosePanel = useCallback(() => {
    setView(closePanel);
  }, []);
  useEscapeCloses(panelOpen(view), onClosePanel);
  const onReview = useCallback((changeId: string) => {
    setView((current) => openReview(stateStore.getState().state, current, changeId) ?? current);
  }, []);
  const onOpenQueue = useCallback(() => {
    const state = stateStore.getState().state;
    setView((current) => {
      const changeId = nextReview(state, current.review);
      return changeId === null ? current : (openReview(state, current, changeId) ?? current);
    });
  }, []);
  const onDecide = useCallback(
    (changeId: string, action: DecisionAction, comment: string) => {
      if (!commands) return;
      void sendDecision(
        commands,
        stateStore.getState().state,
        changeId,
        action,
        comment,
        (attempt) => {
          setAttempts((current) => ({ ...current, [changeId]: attempt }));
        },
      );
    },
    [commands],
  );
  return (
    <Frame
      files={files}
      state={state}
      view={view}
      onNodeTap={onNodeTap}
      canOpenChange={(changeId) => openChange(state, view, changeId) !== null}
      onOpenChange={onOpenChange}
      onClosePanel={onClosePanel}
      onStartAgent={commands?.startAgent}
      canReview={(changeId) => openReview(state, view, changeId) !== null}
      onReview={onReview}
      onOpenQueue={onOpenQueue}
      attempts={attempts}
      onDecide={commands ? onDecide : undefined}
      replayOf={replayBuild ? replaySource.kind : undefined}
      notice={
        replayBuild || error !== null ? (
          <>
            {replayBuild && <p>{replaySource.label}</p>}
            {error !== null && <p role="alert">{error}</p>}
          </>
        ) : undefined
      }
    />
  );
}
