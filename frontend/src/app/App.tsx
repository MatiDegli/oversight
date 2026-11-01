import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useStore } from "zustand";

import type { FileSaved, MergeMode } from "@contracts";

import type { ReplayPlayer } from "../transport";
import type { FileDraft } from "../editor";
import { DecisionContext } from "../review";
import { sendDecision, revertView } from "../review";
import type { DecisionAction, DecisionAttempt, RevertAttempt } from "../review";
import { selectMergeMode, stateStore } from "../state";
import { commandFailure, createCommandClient, replayBuild, replaySource } from "../transport";
import { createFileSession } from "./files";
import { createRecordedFiles } from "./recordedFiles";
import { FilesProvider } from "./FilesContext";
import { useEscapeCloses } from "./escape";
import { mergeModePending, type MergeModeAttempt } from "./shell/mergeModeAttempt";
import { Frame } from "./Frame";
import { usePractice } from "./challenge/usePractice";
import { startAppSession } from "./session";
import {
  clickNode,
  closePanel,
  tabbedView,
  navigateView,
  viewReducer,
  surfaceTab,
  conversationTab,
  openChange,
  openReview,
  panelOpen,
} from "./view";
import type { View } from "./view";

export function App() {
  const [replay, setReplay] = useState<ReplayPlayer | null>(null);
  const [retry, setRetry] = useState(0);
  const [connection, setConnection] = useState<"connecting" | "connected" | "lost">("connecting");
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(tabbedView);
  const [mergeModeAttempt, setMergeModeAttempt] = useState<MergeModeAttempt>();
  const settingMode = useRef(false);
  const [attempts, setAttempts] = useState<Record<string, DecisionAttempt>>({});
  const [revertAttempts, setRevertAttempts] = useState<Record<string, RevertAttempt>>({});
  const reverting = useRef(new Set<string>());
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
  const [files, setFiles] = useState(() =>
    commands
      ? createFileSession(
          commands,
          () => drafts.current,
          () => inFlightSaves.current,
        )
      : createRecordedFiles(() => stateStore.getState().state),
  );
  const onReplayCommit = useCallback((reconcile: (view: View) => View) => {
    setView(reconcile);
    setFiles(createRecordedFiles(() => stateStore.getState().state));
  }, []);
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
    const stopTransport = startAppSession(
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
      setReplay,
    );
    const stop = () => {
      stopTransport();
      setReplay(null);
    };
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
    setView((current) => viewReducer(current, { type: "open", tab: surfaceTab(current, "inbox") }));
  }, []);
  const onOpenSettings = useCallback(() => {
    setView((current) =>
      viewReducer(current, { type: "open", tab: surfaceTab(current, "settings") }),
    );
  }, []);
  const onSetMergeMode = async (mode: MergeMode) => {
    const current = selectMergeMode(state);
    if (
      !commands ||
      !current ||
      current.mode === mode ||
      settingMode.current ||
      mergeModePending(state, mergeModeAttempt)
    )
      return;
    settingMode.current = true;
    const started = { run: state.run, revision: current.revision };
    setMergeModeAttempt({ ...started, phase: "pending" });
    try {
      const response = await commands.setMergeMode({ mode, revision: current.revision });
      setMergeModeAttempt({ ...started, phase: "pending", responseRevision: response.revision });
    } catch (error) {
      setMergeModeAttempt({
        ...started,
        phase: "failed",
        message: commandFailure("Setting the merge mode", error),
      });
    } finally {
      settingMode.current = false;
    }
  };
  const onRevert = async (changeId: string) => {
    const current = stateStore.getState().state;
    const view = revertView(current, changeId, revertAttempts[changeId]);
    if (
      !commands ||
      reverting.current.has(changeId) ||
      view.kind === "none" ||
      view.kind === "pending" ||
      view.kind === "reverted"
    )
      return;
    reverting.current.add(changeId);
    const started: RevertAttempt = {
      run: current.run,
      target: view.body,
      afterSeq: current.lastSeq,
      phase: "pending",
    };
    const record = (attempt: RevertAttempt) => {
      setRevertAttempts((all) => ({ ...all, [changeId]: attempt }));
    };
    record(started);
    try {
      await commands.revertChange(changeId, view.body);
    } catch (error) {
      record({ ...started, phase: "refused", message: commandFailure("Revert", error) });
    } finally {
      reverting.current.delete(changeId);
    }
  };
  const onOpenConversation = useCallback((agentId: string) => {
    setView((current) =>
      viewReducer(current, {
        type: "open",
        tab: conversationTab(current, stateStore.getState().state, agentId),
      }),
    );
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
    <DecisionContext
      value={{
        attempts,
        onDecide: commands ? onDecide : undefined,
        revokeGrant: commands?.revokeGrant,
        replayOf: replayBuild ? replaySource.kind : undefined,
      }}
    >
      <FilesProvider state={state} loadTree={commands?.getTree} openFile={onOpenFile}>
        <Frame
          state={state}
          replay={replay}
          onReplayCommit={onReplayCommit}
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
          onOpenConversation={onOpenConversation}
          onMessageAgent={commands ? (id, text) => commands.messageAgent(id, { text }) : undefined}
          onPauseAgent={commands?.pauseAgent}
          onResumeAgent={commands?.resumeAgent}
          canReview={(changeId) => openReview(state, view, changeId) !== null}
          onReview={onReview}
          onOpenQueue={onOpenQueue}
          onOpenSettings={onOpenSettings}
          onSetMergeMode={
            commands
              ? (mode) => {
                  void onSetMergeMode(mode);
                }
              : undefined
          }
          mergeModeAttempt={mergeModeAttempt}
          attempts={attempts}
          revertAttempts={revertAttempts}
          onRevert={
            commands
              ? (id) => {
                  void onRevert(id);
                }
              : undefined
          }
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
    </DecisionContext>
  );
}
