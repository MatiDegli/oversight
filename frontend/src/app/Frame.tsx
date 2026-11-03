import { presentationFor } from "../teams/presentation";
import { stateStore } from "../state";
import { PreviewPanel } from "./preview/PreviewPanel";
import "./preview/previewChatLayout.css";
import { PreviewActionsContext, type PreviewActions } from "../agents";
// eslint-disable-next-line no-restricted-imports -- Shared event-backed admission projection.
import { leadCapacity } from "../agents/chatLeadSelectors";
import type { MergeMode, StartAgent, StartLead, StopLead } from "@contracts";
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ReplayPlayer } from "../transport";
import type { FileAccess } from "../editor";
import type { DecisionAction, DecisionAttempt, RevertAttempt } from "../review";
import type { State } from "../state";
import type { View, ViewAction, FocusTab } from "./view";
import { panelOpen } from "./view";
import { useEscapeCloses } from "./escape";
import { useFunctionTask } from "./useFunctionTask";
import { TopBar } from "./TopBar";
import { AgentsPanel, AgentsRail } from "./shell/Agents";
import type { MergeModeAttempt } from "./shell/mergeModeAttempt";
import { ChatMergeChip } from "./shell/ChatMerge";
import { NewLead } from "./shell/NewLead";
// eslint-disable-next-line no-restricted-imports -- K2a owns the New lead form and its draft transfer.
import { leadDraftStore } from "../agents/leadDraftModel";
import { ChatEmpty, ChatShell } from "./shell/ChatShell";
import { FocusPanel } from "./shell/FocusPanel";
import { LazyPanel } from "./shell/LazyPanel";
import { LeftRail, Sidebar, ShortcutsSheet } from "./shell/Navigation";
import { BottomDock, StatusLine } from "./shell/Status";
import { useShell } from "./shell/useShell";
import { useChatReviewNavigation } from "./chatReviewNavigation";
import { PaletteMount } from "./palette/PaletteMount";
import "./shell/shell.css";
import "./shell/chatShell.css";
import "./shell/life.css";
import "./challenge/phone.css";
import { useMergeArrival } from "./useMergeArrival";
import { usePhonePractice } from "./challenge/usePhonePractice";
const OversightStage = lazy(() =>
  import("./shell/OversightStage").then(async (module) => {
    await module.fieldReady;
    return { default: module.OversightStage };
  }),
);
const ReplayBar = lazy(() => import("./replay/ReplayBar"));

export interface FrameProps {
  previewActions?: PreviewActions;
  previewHost?: import("./preview/previewHost").PreviewHost;
  chatState?: State;
  readChatState?: () => State | undefined;
  historySession?: { eventSeq: number; back: () => HTMLElement | null };
  onViewMoment?: (seq: number) => void;
  onViewHighlight?: (nodeIds: string[], key: string) => void;
  onViewFile?: (changeId: string, path: string) => void;
  replay?: ReplayPlayer | null;
  onReplayCommit?: (reconcile: (view: View) => View) => void;
  practice?: { active: boolean; open: () => void; content: ReactNode; focus: FocusTab };
  state: State;
  view: View;
  files?: FileAccess;
  onNodeTap: (nodeId: string) => void;
  /** Present only for a change the card can open; a card never shows a button that does nothing. */
  canOpenChange: (changeId: string) => boolean;
  onOpenChange: (changeId: string) => void;
  onClosePanel: () => void;
  /** Absent in a recorded run, which has no backend: the agents panel then offers no new agent. */
  onStopLead?: (id: string, body: StopLead) => Promise<unknown>;
  onStartLead?: (body: StartLead) => Promise<unknown>;
  onStartAgent?: (body: StartAgent) => Promise<unknown>;
  onStopAgent?: (agentId: string) => Promise<unknown>;
  onOpenConversation?: (agentId: string) => void;
  onMessageLead?: (agentId: string, text: string) => Promise<unknown>;
  onMessageAgent?: (agentId: string, text: string) => Promise<unknown>;
  onPauseAgent?: (agentId: string) => Promise<unknown>;
  onResumeAgent?: (agentId: string) => Promise<unknown>;
  /** True for a proposed change: its card offers Review change instead of the live diff. */
  canReview?: (changeId: string) => boolean;
  onReview?: (changeId: string) => void;
  /** The queue counter's click: review of the next proposed change. */
  onOpenQueue?: () => void;
  onOpenSettings?: () => void;
  onOpenUsage?: () => void;
  onSetMergeMode?: (mode: MergeMode) => void;
  mergeModeAttempt?: MergeModeAttempt;
  /** The person's last decision per change. */
  attempts?: Readonly<Record<string, DecisionAttempt>>;
  revertAttempts?: Readonly<Record<string, RevertAttempt>>;
  onRevert?: (changeId: string) => void;
  /** Absent in a recorded run, which sends no commands: review then shows the recorded decision. */
  onDecide?: (changeId: string, action: DecisionAction, comment: string) => void;
  /** What the replay plays, from its source: review and the new-agent box word themselves with it. */
  replayOf?: string;
  /** Overlay lines on the graph, such as the replay label and a transport failure. */
  notice?: ReactNode;
  replayLabel?: string;
  connection?: "connecting" | "connected" | "lost";
  onRetry?: () => void;
  onTabAction?: (action: ViewAction) => void;
}

/** Fixed zones compose around one persistent graph instance. */
export function Frame(input: FrameProps) {
  const props: FrameProps = input.historySession
    ? {
        ...input,
        onStartAgent: undefined,
        onStartLead: undefined,
        onStopLead: undefined,
        onStopAgent: undefined,
        onMessageLead: undefined,
        onMessageAgent: undefined,
        onPauseAgent: undefined,
        onResumeAgent: undefined,
        onSetMergeMode: undefined,
        onRevert: undefined,
        onDecide: undefined,
      }
    : input;
  const { state, onClosePanel } = props;
  const arrival = useMergeArrival(state);
  const phone = usePhonePractice(props.practice?.active ?? false);
  // Availability stays fixed throughout a replay session, so entry, exit and resizing
  // keep the same graph owner. Only the practice composition knows about stacked overlays.
  const view =
    phone && props.practice
      ? {
          ...props.view,
          ...props.practice.focus,
          tabs: [props.practice.focus],
          activeTab: "practice",
        }
      : props.view;
  const [newLead, setNewLead] = useState(false);
  const [hiddenChatView, setHiddenChatView] = useState<View | null>(null);
  const leadInput = useRef<HTMLTextAreaElement>(null);
  // Keep shared tabs; opening or activating a tab creates a new View and reveals the panel.
  const shell = useShell(panelOpen(view), onClosePanel, hiddenChatView === view);
  const open = shell.panelOpen;
  const previewSlot = useMemo(
    () =>
      props.previewHost
        ? {
            subscribe: (listener: () => void) =>
              props.previewHost?.subscribe(listener) ?? (() => {}),
            snapshot: props.previewHost.snapshot,
            content: (
              <PreviewPanel
                controller={props.previewHost.controller}
                clock={props.previewHost.clock}
                elapsed={props.previewHost.elapsed}
                identity={(id) => presentationFor(stateStore.getState().state, id)}
              />
            ),
          }
        : undefined,
    [props.previewHost],
  );
  useEffect(() => {
    void props.previewHost?.navigate();
  }, [props.previewHost, view.activeTab, shell.layout, props.historySession]);
  useEscapeCloses(open, onClosePanel);
  const showNewLead = newLead && !open && shell.layout === "Chat";
  const chatReview = useChatReviewNavigation(state, view, shell, props.onTabAction);
  const task = useFunctionTask(
    state,
    view,
    props.onStartAgent !== undefined && shell.layout === "Oversight",
  );
  const newTask = () => {
    void props.previewHost?.navigate();
    setNewLead(false);
    setHiddenChatView(view);
    requestAnimationFrame(() => task.inputRef.current?.focus());
  };
  const openNewLead = () => {
    void props.previewHost?.navigate();
    if (leadCapacity(state).atCap) return;
    setHiddenChatView(view);
    setNewLead(true);
    requestAnimationFrame(() => {
      leadInput.current?.focus();
      const list = leadInput.current
        ?.closest(".shell-frame")
        ?.querySelector<HTMLElement>(".chat-agents .chat-side-body");
      if (list) list.scrollTop = list.scrollHeight;
    });
  };
  const layoutKey =
    shell.sidebar === null &&
    !shell.dock &&
    !shell.maximized &&
    !task.expanded &&
    shell.layoutRevision === 0
      ? ""
      : `${shell.sidebar ?? ""}:${String(shell.dock)}:${String(shell.maximized)}:${String(task.expanded)}:${String(shell.layoutRevision)}`;
  return (
    <div
      className={`shell-frame flex h-full flex-col bg-background text-text ${phone ? "practice-phone" : ""}`}
    >
      <TopBar
        state={state}
        layout={shell.layout}
        onLayout={(layout) => {
          if (view.referenceHighlight) props.onTabAction?.({ type: "clear-reference" });
          shell.setLayout(layout);
        }}
        onOpenQueue={props.onOpenQueue}
        onOpenSettings={props.onOpenSettings}
        onOpenUsage={props.onOpenUsage}
        onOpenPalette={shell.openPalette}
        practice={props.practice}
      />
      {props.historySession && (
        <div className="view-history-banner" role="status">
          Viewing event {props.historySession.eventSeq} ·{" "}
          <button
            type="button"
            onClick={() => {
              const origin = props.historySession?.back();
              // Restore focus only for Back, after React commits the restored conversation.
              requestAnimationFrame(() => {
                if (origin?.isConnected) origin.focus();
              });
            }}
          >
            Back
          </button>
        </div>
      )}
      <PreviewActionsContext value={props.previewActions ?? null}>
        <ChatShell
          previewSlot={previewSlot}
          state={props.chatState ?? state}
          readOnly={Boolean(props.historySession)}
          active={shell.layout === "Chat"}
          selectedAgentId={
            !open || showNewLead
              ? undefined
              : view.tabs?.find((tab) => tab.id === view.activeTab)?.conversation
          }
          onNewTask={newTask}
          onNewLead={openNewLead}
          leadDraft={showNewLead}
          onOpenConversation={
            props.onOpenConversation
              ? (id) => {
                  setNewLead(false);
                  props.onOpenConversation?.(id);
                }
              : undefined
          }
          rail={
            <LeftRail
              watching={!open}
              sidebar={shell.sidebar}
              onSidebar={shell.openSidebar}
              onWatch={onClosePanel}
              onShortcuts={() => {
                shell.setShortcuts(true);
              }}
            />
          }
          sidebar={
            shell.sidebar && (
              <Sidebar
                section={shell.sidebar}
                onClose={() => {
                  shell.openSidebar(shell.sidebar);
                }}
              />
            )
          }
          agents={
            open && !task.expanded ? (
              <AgentsRail state={state} />
            ) : (
              <AgentsPanel {...props} mergeLabels={arrival.labels} inputRef={task.inputRef} />
            )
          }
        >
          <div className={`shell-center ${shell.maximized && open ? "is-maximized" : ""}`}>
            <div className="shell-stage">
              {open && (
                <FocusPanel
                  {...props}
                  view={view}
                  chat={shell.layout === "Chat"}
                  onGraph={chatReview.onGraph}
                  onViewHighlight={(ids, key) => {
                    props.onViewHighlight?.(ids, key);
                    shell.setLayout("Oversight");
                  }}
                  practiceOnly={phone}
                  startAgentAction={task.action}
                  maximized={shell.maximized}
                  onMaximize={shell.toggleMaximize}
                />
              )}
              {showNewLead && (
                <NewLead
                  state={state}
                  onStart={props.onStartLead}
                  inputRef={leadInput}
                  onOpenSettings={props.onOpenSettings}
                  onClose={() => {
                    setNewLead(false);
                  }}
                  onTaskInstead={() => {
                    leadDraftStore.getState().moveToTask();
                    newTask();
                  }}
                />
              )}
              {shell.layout === "Chat" && !open && !newLead && (
                <ChatEmpty
                  readOnly={Boolean(props.historySession)}
                  onNewLead={openNewLead}
                  actionAccessory={
                    <ChatMergeChip state={state} onOpenSettings={props.onOpenSettings} />
                  }
                  state={state}
                  onStart={props.onStartAgent}
                  replayOf={props.replayOf}
                  inputRef={task.inputRef}
                />
              )}
              <div
                className="oversight-stage"
                hidden={shell.layout === "Chat"}
                inert={shell.layout === "Chat"}
              >
                {shell.oversightVisited && (
                  <LazyPanel frame={<GraphLoadingFrame />}>
                    <OversightStage
                      practice={Boolean(props.practice)}
                      stacked={phone}
                      arrival={arrival}
                      state={state}
                      view={chatReview.graphView}
                      onNodeTap={phone ? () => undefined : props.onNodeTap}
                      layoutKey={`${layoutKey}${phone ? ":phone" : ""}`}
                    />
                  </LazyPanel>
                )}
              </div>
            </div>
            {shell.dock && <BottomDock onClose={shell.toggleDock} />}
            {props.replay && props.onReplayCommit && !props.practice?.active && (
              <Suspense fallback={<div className="hidden h-[44px] shrink-0 min-[1280px]:block" />}>
                <ReplayBar
                  key={props.replay.id}
                  player={props.replay}
                  chatState={props.chatState}
                  readChatState={props.readChatState}
                  onCommit={props.onReplayCommit}
                />
              </Suspense>
            )}
          </div>
        </ChatShell>
      </PreviewActionsContext>
      <StatusLine {...props} dock={shell.dock} onToggleDock={shell.toggleDock} />
      <p className="narrow-notice" role="status">
        This window is too narrow. Widen it to at least 1280 px.
        {props.practice && (
          <button
            type="button"
            className="phone-practice-entry underline"
            onClick={props.practice.open}
          >
            Try two-change practice
          </button>
        )}
      </p>
      {shell.shortcuts && (
        <ShortcutsSheet
          onClose={() => {
            shell.setShortcuts(false);
          }}
        />
      )}
      {!phone && (
        <PaletteMount
          frame={props}
          shell={shell}
          onNode={props.onNodeTap}
          onFocusTask={
            props.onStartAgent && (!open || task.expanded)
              ? () => {
                  requestAnimationFrame(() => task.inputRef.current?.focus());
                }
              : undefined
          }
        />
      )}
    </div>
  );
}

/** Keep the graph/field host anatomy available while the rendering module loads.
 * These are empty, busy surfaces: no probes or rendering contexts are fabricated.
 */
function GraphLoadingFrame({ children }: { children?: ReactNode }) {
  return (
    <section className="graph-stage relative flex-1 bg-ground" aria-label="Graph stage">
      <div className="relative h-full w-full">
        <canvas aria-hidden="true" className="pointer-events-none absolute inset-0 size-full" />
        <div
          role="img"
          aria-label="Repository graph"
          aria-busy="true"
          className="relative h-full w-full"
        />
      </div>
      <div className="absolute inset-0 p-6 text-muted">
        {children ?? <p role="status">Loading repository graph…</p>}
      </div>
    </section>
  );
}
