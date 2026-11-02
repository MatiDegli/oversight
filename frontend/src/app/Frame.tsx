import type { MergeMode, StartAgent } from "@contracts";
import { lazy, Suspense, type ReactNode } from "react";
import type { ReplayPlayer } from "../transport";
import type { FileAccess } from "../editor";
import type { DecisionAction, DecisionAttempt, RevertAttempt } from "../review";
import type { State } from "../state";
import type { View, ViewAction, FocusTab } from "./view";
import { panelOpen } from "./view";
import { useFunctionTask } from "./useFunctionTask";
import { TopBar } from "./TopBar";
import { AgentsPanel, AgentsRail } from "./shell/Agents";
import type { MergeModeAttempt } from "./shell/mergeModeAttempt";
import { ChatMergeChip } from "./shell/ChatMerge";
import { ChatEmpty, ChatShell } from "./shell/ChatShell";
import { FocusPanel } from "./shell/FocusPanel";
import { LazyPanel } from "./shell/LazyPanel";
import { LeftRail, Sidebar, ShortcutsSheet } from "./shell/Navigation";
import { BottomDock, StatusLine } from "./shell/Status";
import { useShell } from "./shell/useShell";
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
  onStartAgent?: (body: StartAgent) => Promise<unknown>;
  onStopAgent?: (agentId: string) => Promise<unknown>;
  onOpenConversation?: (agentId: string) => void;
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
export function Frame(props: FrameProps) {
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
  const open = panelOpen(view);
  const shell = useShell(open, onClosePanel);
  const task = useFunctionTask(
    state,
    view,
    props.onStartAgent !== undefined && shell.layout === "Oversight",
  );
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
        onLayout={shell.setLayout}
        onOpenQueue={props.onOpenQueue}
        onOpenSettings={props.onOpenSettings}
        onOpenUsage={props.onOpenUsage}
        onOpenPalette={shell.openPalette}
        practice={props.practice}
      />
      <ChatShell
        active={shell.layout === "Chat"}
        selectedAgentId={view.tabs?.find((tab) => tab.id === view.activeTab)?.conversation}
        onOpenConversation={props.onOpenConversation}
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
                practiceOnly={phone}
                startAgentAction={task.action}
                maximized={shell.maximized}
                onMaximize={shell.toggleMaximize}
              />
            )}
            {shell.layout === "Chat" && !open && (
              <ChatEmpty
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
                    view={view}
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
                onCommit={props.onReplayCommit}
              />
            </Suspense>
          )}
        </div>
      </ChatShell>
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
