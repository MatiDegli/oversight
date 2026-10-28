import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useStore } from "zustand";

import type { FileSaved } from "@contracts";

import type { FileDraft } from "../editor";
import { sendDecision } from "../review";
import type { DecisionAction, DecisionAttempt } from "../review";
import { stateStore } from "../state";
import { createCommandClient, replayBuild, replaySource } from "../transport";
import { createFileSession } from "./files";
import { createRecordedFiles } from "./recordedFiles";
import { FilesProvider } from "./FilesContext";
import { useEscapeCloses } from "./escape";
import { Frame } from "./Frame";
import { usePractice } from "./challenge/usePractice";
import { startAppSession } from "./session";
import {
  clickNode,
  closePanel,
  tabbedView,
  navigateView,
  viewReducer,
  nextReview,
  openChange,
  openReview,
  panelOpen,
} from "./view";
import type { View } from "./view";

export function App() {
  const [retry, setRetry] = useState(0);
  const [connection, setConnection] = useState<"connecting" | "connected" | "lost">("connecting");
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(tabbedView);
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
      : createRecordedFiles(() => stateStore.getState().state),
  );
  const state = useStore(stateStore, (store) => store.state);
  const stopSession = useRef(() => {});
  const practice = usePractice(
    setView,
    () => {
      stopSession.current();
    },
    () => {
      setRetry((value) => value + 1);
    },
    replayBuild,
    replaySource,
  );
  useEffect(() => {
    const stop = startAppSession(
      document.getElementById("root"),
      (failure) => {
        setConnection(failure === null ? "connected" : "lost");
        setError(
          failure === null ? null : failure instanceof Error ? failure.message : "Transport failed",
        );
      },
      () => {
        setConnection("connected");
      },
    );
    stopSession.current = stop;
    return stop;
  }, [retry]);
  // The graph keeps one tap handler; it reads the store when the tap happens.
  const onNodeTap = useCallback(
    (nodeId: string) => {
      setView((current) =>
        navigateView(
          current,
          clickNode(stateStore.getState().state, current, nodeId, commands !== null),
          stateStore.getState().state,
        ),
      );
    },
    [commands],
  );
  const onOpenChange = useCallback((changeId: string) => {
    setView((current) =>
      navigateView(
        current,
        openChange(stateStore.getState().state, current, changeId) ?? current,
        stateStore.getState().state,
      ),
    );
  }, []);
  const onClosePanel = useCallback(() => {
    setView(closePanel);
  }, []);
  useEscapeCloses(panelOpen(view), onClosePanel);
  const onReview = useCallback((changeId: string) => {
    setView((current) =>
      navigateView(
        current,
        openReview(stateStore.getState().state, current, changeId) ?? current,
        stateStore.getState().state,
      ),
    );
  }, []);
  const onOpenQueue = useCallback(() => {
    const state = stateStore.getState().state;
    setView((current) => {
      const changeId = nextReview(state, current.review);
      return changeId === null
        ? current
        : navigateView(current, openReview(state, current, changeId) ?? current, state);
    });
  }, []);
  const onDecide = useCallback(
    (changeId: string, action: DecisionAction, comment: string) => {
      if (!commands) return;
      const change = state.changes[changeId];
      const proposal = change?.proposal;
      const base = change ? state.agents[change.agent_id]?.base_commit : null;
      const head = proposal?.head_commit ?? base;
      const target = head ? { revision: proposal?.revision ?? 0, head_commit: head } : undefined;
      void sendDecision(
        commands,
        state,
        changeId,
        action,
        comment,
        (attempt) => {
          setAttempts((current) => ({ ...current, [changeId]: attempt }));
        },
        target,
      );
    },
    [commands, state],
  );
  const onOpenFile = useCallback((path: string) => {
    const state = stateStore.getState().state;
    const keepComment = Boolean(
      document.activeElement?.closest('[aria-label="Review panel"] textarea'),
    );
    setView((current) => {
      const module = state.graph?.nodes.find(
        (node) => node.path === path && node.kind === "module",
      );
      const next = navigateView(
        current,
        {
          selected: module?.id ?? null,
          diff: null,
          review: null,
          file: { path, agentId: null, startLine: 1, endLine: module?.end_line ?? 1 },
        },
        state,
      );
      return keepComment && current.activeTab
        ? viewReducer(next, { type: "activate", id: current.activeTab })
        : next;
    });
  }, []);
  return (
    <FilesProvider state={state} loadTree={commands?.getTree} openFile={onOpenFile}>
      <Frame
        state={state}
        practice={practice}
        files={files}
        view={view}
        onNodeTap={onNodeTap}
        canOpenChange={(changeId) => openChange(state, view, changeId) !== null}
        onOpenChange={onOpenChange}
        onClosePanel={onClosePanel}
        onTabAction={(action) => {
          setView((current) => viewReducer(current, action));
        }}
        onStartAgent={commands?.startAgent}
        onStopAgent={commands?.stopAgent}
        canReview={(changeId) => openReview(state, view, changeId) !== null}
        onReview={onReview}
        onOpenQueue={onOpenQueue}
        attempts={attempts}
        onDecide={commands ? onDecide : undefined}
        replayOf={replayBuild ? replaySource.kind : undefined}
        replayLabel={replayBuild ? replaySource.label : undefined}
        connection={replayBuild ? undefined : connection}
        onRetry={() => {
          setConnection("connecting");
          setError(null);
          setRetry((value) => value + 1);
        }}
        notice={error !== null ? <p role="alert">{error}</p> : undefined}
      />
    </FilesProvider>
  );
}
