import { useCallback, useEffect, useMemo, useState } from "react";
import { useStore } from "zustand";

import { sendDecision } from "../review";
import type { DecisionAction, DecisionAttempt, DecisionClient } from "../review";
import { stateStore } from "../state";
import { createCommandClient, replayBuild, replaySource } from "../transport";
import { Frame } from "./Frame";
import { startAppSession } from "./session";
import {
  clickNode,
  closePanel,
  closesPanel,
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
  // The replay build has no backend: it never sends a command, and review shows recorded decisions.
  const commands = useMemo<DecisionClient | null>(
    () => (replayBuild ? null : createCommandClient()),
    [],
  );
  const state = useStore(stateStore, (store) => store.state);
  useEffect(() => {
    return startAppSession(document.getElementById("root"), (failure) => {
      setError(
        failure === null ? null : failure instanceof Error ? failure.message : "Transport failed",
      );
    });
  }, []);
  // Only while the panel is open: Escape closes it unless the editor inside took the key.
  const open = panelOpen(view);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (closesPanel(event)) setView(closePanel);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  // The graph keeps one tap handler; it reads the store when the tap happens.
  const onNodeTap = useCallback((nodeId: string) => {
    setView((current) => clickNode(stateStore.getState().state, current, nodeId));
  }, []);
  const onOpenChange = useCallback((changeId: string) => {
    setView((current) => openChange(stateStore.getState().state, current, changeId) ?? current);
  }, []);
  const onClosePanel = useCallback(() => {
    setView(closePanel);
  }, []);
  const onReview = useCallback((changeId: string) => {
    setView((current) => openReview(stateStore.getState().state, current, changeId) ?? current);
  }, []);
  const onOpenQueue = useCallback(() => {
    const state = stateStore.getState().state;
    const changeId = nextReview(state);
    if (changeId !== null) setView((current) => openReview(state, current, changeId) ?? current);
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
      state={state}
      view={view}
      onNodeTap={onNodeTap}
      canOpenChange={(changeId) => openChange(state, view, changeId) !== null}
      onOpenChange={onOpenChange}
      onClosePanel={onClosePanel}
      canReview={(changeId) => openReview(state, view, changeId) !== null}
      onReview={onReview}
      onOpenQueue={onOpenQueue}
      attempts={attempts}
      onDecide={commands ? onDecide : undefined}
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
