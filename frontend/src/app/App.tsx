import { useCallback, useEffect, useMemo, useState } from "react";
import { useStore } from "zustand";

import { stateStore } from "../state";
import { createCommandClient, replayBuild, replaySource } from "../transport";
import { Frame } from "./Frame";
import { startAppSession } from "./session";
import { clickNode, closePanel, closesPanel, initialView, openChange } from "./view";
import type { View } from "./view";

export function App() {
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(initialView);
  // The replay build has no backend: it never creates the command client, and starts no agent.
  const commands = useMemo(() => (replayBuild ? null : createCommandClient()), []);
  const state = useStore(stateStore, (store) => store.state);
  useEffect(() => {
    return startAppSession(document.getElementById("root"), (failure) => {
      setError(
        failure === null ? null : failure instanceof Error ? failure.message : "Transport failed",
      );
    });
  }, []);
  // Only while the panel is open: Escape closes it unless the editor inside took the key.
  const panelOpen = view.diff !== null;
  useEffect(() => {
    if (!panelOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (closesPanel(event)) setView(closePanel);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [panelOpen]);
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
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={onNodeTap}
      canOpenChange={(changeId) => openChange(state, view, changeId) !== null}
      onOpenChange={onOpenChange}
      onClosePanel={onClosePanel}
      onStartAgent={commands?.startAgent}
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
