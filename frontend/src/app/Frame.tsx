import type { MergeMode, StartAgent } from "@contracts";
import type { ReactNode } from "react";
import type { FileAccess } from "../editor";
import type { DecisionAction, DecisionAttempt } from "../review";
import type { State } from "../state";
import type { View, ViewAction, FocusTab } from "./view";
import { panelOpen } from "./view";
import { useFunctionTask } from "./useFunctionTask";
import { TopBar } from "./TopBar";
import { AgentsPanel, AgentsRail } from "./shell/Agents";
import type { MergeModeAttempt } from "./shell/SettingsPanel";
import { FocusPanel } from "./shell/FocusPanel";
import { GraphStage } from "./shell/GraphStage";
import { LeftRail, Sidebar, ShortcutsSheet } from "./shell/Navigation";
import { BottomDock, StatusLine } from "./shell/Status";
import { useShell } from "./shell/useShell";
import { PaletteMount } from "./palette/PaletteMount";
import "./shell/shell.css";
import "./challenge/phone.css";
import { usePhonePractice } from "./challenge/usePhonePractice";
import { PracticeGraphStage } from "./challenge/PracticeGraphStage";
export interface FrameProps {
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
  onPauseAgent?: (agentId: string) => Promise<unknown>;
  onResumeAgent?: (agentId: string) => Promise<unknown>;
  /** True for a proposed change: its card offers Review change instead of the live diff. */
  canReview?: (changeId: string) => boolean;
  onReview?: (changeId: string) => void;
  /** The queue counter's click: review of the next proposed change. */
  onOpenQueue?: () => void;
  onOpenSettings?: () => void;
  onSetMergeMode?: (mode: MergeMode) => void;
  mergeModeAttempt?: MergeModeAttempt;
  /** The person's last decision per change. */
  attempts?: Readonly<Record<string, DecisionAttempt>>;
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
  const phone = usePhonePractice(props.practice?.active ?? false);
  // Availability stays fixed throughout a replay session, so entry, exit and resizing
  // keep the same graph owner. Only the practice composition knows about stacked overlays.
  const Stage = props.practice ? PracticeGraphStage : GraphStage;
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
  const task = useFunctionTask(state, view, props.onStartAgent !== undefined);
  const shell = useShell(open, onClosePanel);
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
        onOpenQueue={props.onOpenQueue}
        onOpenSettings={props.onOpenSettings}
        onOpenPalette={shell.openPalette}
        practice={props.practice}
      />
      <div className="shell-workspace">
        <LeftRail
          watching={!open}
          sidebar={shell.sidebar}
          onSidebar={shell.openSidebar}
          onWatch={onClosePanel}
          onShortcuts={() => {
            shell.setShortcuts(true);
          }}
        />
        {shell.sidebar && (
          <Sidebar
            section={shell.sidebar}
            onClose={() => {
              shell.openSidebar(shell.sidebar);
            }}
          />
        )}
        <div className={`shell-center ${shell.maximized && open ? "is-maximized" : ""}`}>
          <div className="shell-stage">
            {open && (
              <FocusPanel
                {...props}
                view={view}
                practiceOnly={phone}
                startAgentAction={task.action}
                maximized={shell.maximized}
                onMaximize={shell.toggleMaximize}
              />
            )}
            <Stage
              state={state}
              view={view}
              onNodeTap={phone ? () => undefined : props.onNodeTap}
              {...(props.practice ? { stacked: phone } : {})}
              layoutKey={`${layoutKey}${phone ? ":phone" : ""}`}
            />
          </div>
          {shell.dock && <BottomDock onClose={shell.toggleDock} />}
        </div>
        {open && !task.expanded ? (
          <AgentsRail state={state} />
        ) : (
          <AgentsPanel {...props} inputRef={task.inputRef} />
        )}
      </div>
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
