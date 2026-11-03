import { createPreviewHost, previewClock } from "./preview/previewHost";
import { parsePreviewDetails, parsePreviewStatus } from "./preview/previewTypes";
import { createLivePreviewPort } from "../transport";
import { type ContextType, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useStore } from "zustand";

import type { FileSaved, MergeMode, Plan, QueuedTask } from "@contracts";

import type { ReplayPlayer } from "../transport";
import type { FileDraft } from "../editor";
import { DecisionContext } from "../review";
import { sendDecision, revertView } from "../review";
import type { DecisionAction, DecisionAttempt, RevertAttempt } from "../review";
import { selectMergeMode, stateStore, type State } from "../state";
import { commandFailure, createCommandClient, replayBuild, replaySource } from "../transport";
import { createFileSession } from "./files";
import { createRecordedFiles } from "./recordedFiles";
import { FilesProvider } from "./FilesContext";
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
  openChangeFile,
  reconcileReferenceHighlight,
  openReview,
} from "./view";
import { useViewHistory } from "./useViewHistory";
import type { View } from "./view";

type PlanCapability = NonNullable<ContextType<typeof DecisionContext>["planActions"]>;
type PlanAttempt = PlanCapability["attempts"][string];
type PlanAction = Parameters<PlanCapability["send"]>[1];

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
  const [planAttempts, setPlanAttempts] = useState<Record<string, PlanAttempt>>({});
  const sendingPlans = useRef(new Set<string>());
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
  // Preview lifecycle envelopes retain reducer bookkeeping without redrawing the working UI.
  const [displayedState] = useState(() => {
    let previous: State | undefined;
    return ({ state: next }: { state: State }) => {
      if (
        previous &&
        !next.gap &&
        Object.keys(next).every((key) => {
          const field = key as keyof State;
          return (
            field === "lastSeq" ||
            field === "clock" ||
            field === "gap" ||
            next[field] === previous?.[field]
          );
        })
      )
        return previous;
      previous = next;
      return next;
    };
  });
  const state = useStore(stateStore, displayedState);
  const { history, session: historical } = useViewHistory(replay);
  const readChatState = useCallback(() => history?.getSnapshot()?.chat, [history]);
  useEffect(() => {
    // A no-op setter can leave a pending lane across consecutive synchronous store commits.
    // Reconcile only invalid targets; unchanged references need no React update.
    if (reconcileReferenceHighlight(state, view) !== view)
      setView((current) => reconcileReferenceHighlight(state, current));
  }, [state, view]);
  const previewPermission = useRef(false);
  useEffect(() => {
    previewPermission.current = commands !== null && historical === null;
  }, [commands, historical]);
  const [previewHost] = useState(() =>
    replayBuild || typeof window === "undefined"
      ? undefined
      : createPreviewHost(
          createLivePreviewPort(window.location.origin, {
            details: parsePreviewDetails,
            status: parsePreviewStatus,
          }),
          previewClock,
          { run: stateStore.getState().state.run?.run_id ?? "", source: "live" },
          () => stateStore.getState().state,
          () => previewPermission.current,
        ),
  );
  useEffect(() => {
    void previewHost?.setIdentity({
      run: state.run?.run_id ?? "",
      source: replay ? `replay:${String(replay.id)}` : "live",
    });
    if (historical) void previewHost?.navigate();
  }, [previewHost, state.run?.run_id, replay, historical]);
  const previewMounted = useRef(false);
  useEffect(() => {
    if (!previewHost) return;
    previewMounted.current = true;
    const hide = () => {
      previewHost.dispose();
    };
    window.addEventListener("pagehide", hide);
    return () => {
      window.removeEventListener("pagehide", hide);
      previewMounted.current = false;
      queueMicrotask(() => {
        if (!previewMounted.current) previewHost.dispose();
      });
    };
  }, [previewHost]);
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
      (event) => {
        previewHost?.accept(event);
      },
    );
    const stop = () => {
      stopTransport();
      setReplay(null);
    };
    stopSession.current = stop;
    return stop;
  }, [retry, previewHost]);
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
  const onOpenUsage = useCallback(() => {
    setView((current) => viewReducer(current, { type: "open", tab: surfaceTab(current, "usage") }));
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
  const onPlanAction = async (plan: Plan, action: PlanAction, queue?: QueuedTask, comment = "") => {
    const key = `${state.run?.run_id ?? ""}:${plan.plan_id}`;
    if (!commands || state.run !== stateStore.getState().state.run || sendingPlans.current.has(key))
      return;
    sendingPlans.current.add(key);
    const attempt: PlanAttempt = {
      run: state.run,
      planId: plan.plan_id,
      action,
      revision: plan.revision,
      queueRevision: queue?.revision,
      queueId: queue?.queued_task_id,
      phase: "pending",
    };
    setPlanAttempts((all) => ({ ...all, [plan.plan_id]: attempt }));
    try {
      if (action === "queue") await commands.startPlan(plan.plan_id, { revision: plan.revision });
      if (action === "reject")
        await commands.rejectPlan(plan.plan_id, {
          revision: plan.revision,
          ...(comment.trim() ? { comment: comment.trim() } : {}),
        });
      if (action === "cancel" && queue)
        await commands.cancelQueue(queue.queued_task_id, { revision: queue.revision });
    } catch (error) {
      const current = stateStore.getState().state;
      if (current.run === attempt.run)
        setPlanAttempts((all) =>
          all[plan.plan_id] !== attempt
            ? all
            : {
                ...all,
                [plan.plan_id]: {
                  ...attempt,
                  phase: "failed",
                  message: commandFailure(
                    action === "cancel"
                      ? "Taking the plan out of the queue"
                      : action === "reject"
                        ? "Reject plan"
                        : "Queue plan",
                    error,
                  ),
                },
              },
        );
    } finally {
      sendingPlans.current.delete(key);
    }
  };
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
  const canCommand = commands !== null && historical === null;
  return (
    <DecisionContext
      value={{
        attempts,
        onDecide: canCommand ? onDecide : undefined,
        revokeGrant: canCommand ? commands.revokeGrant : undefined,
        planActions: canCommand
          ? {
              attempts: planAttempts,
              send: (plan, action, queue, comment) => {
                void onPlanAction(plan, action, queue, comment);
              },
            }
          : undefined,
        replayOf: replayBuild ? replaySource.kind : undefined,
      }}
    >
      <FilesProvider
        state={state}
        loadTree={canCommand ? commands.getTree : undefined}
        openFile={onOpenFile}
      >
        <Frame
          previewHost={previewHost}
          previewActions={{
            state: historical?.chat ?? state,
            readState: () => stateStore.getState().state,
            readOnlyReason: !canCommand
              ? "Preview and review are unavailable in replay."
              : undefined,
            onPreview:
              canCommand && previewHost
                ? (request) => {
                    void previewHost.activate(request);
                  }
                : undefined,
            onReview: canCommand
              ? (candidate) => {
                  void previewHost?.navigate();
                  onReview(candidate.change_id);
                }
              : undefined,
          }}
          state={state}
          chatState={historical?.chat}
          readChatState={readChatState}
          historySession={
            historical && history
              ? { eventSeq: historical.eventSeq, back: history.back }
              : undefined
          }
          onViewMoment={history?.open}
          onViewHighlight={(nodeIds, key) => {
            setView((current) => ({
              ...current,
              referenceHighlight: {
                nodeIds,
                frameKey: `${key}:${String(performance.now())}`,
                runId: state.run?.run_id ?? "",
              },
            }));
          }}
          onViewFile={(changeId, path) => {
            setView((current) => {
              const next = openChangeFile(state, changeId, path);
              return next ? navigateView(current, next, state) : current;
            });
          }}
          replay={replay}
          onReplayCommit={onReplayCommit}
          practice={practice}
          files={historical ? createRecordedFiles(() => stateStore.getState().state) : files}
          view={view}
          onNodeTap={onNodeTap}
          canOpenChange={(changeId) => openChange(state, view, changeId) !== null}
          onOpenChange={onOpenChange}
          onClosePanel={onClosePanel}
          onTabAction={(action) => {
            setView((current) => viewReducer(current, action));
          }}
          onStartAgent={commands?.startAgent}
          onStartLead={commands?.startLead}
          onStopLead={commands?.stopLead}
          onStopAgent={commands?.stopAgent}
          onOpenConversation={onOpenConversation}
          onMessageLead={commands ? (id, text) => commands.messageLead(id, { text }) : undefined}
          onMessageAgent={commands ? (id, text) => commands.messageAgent(id, { text }) : undefined}
          onPauseAgent={commands?.pauseAgent}
          onResumeAgent={commands?.resumeAgent}
          canReview={(changeId) => openReview(state, view, changeId) !== null}
          onReview={onReview}
          onOpenQueue={onOpenQueue}
          onOpenSettings={onOpenSettings}
          onOpenUsage={replayBuild ? undefined : onOpenUsage}
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
