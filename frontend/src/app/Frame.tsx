import type { StartAgent } from "@contracts";
import type { ReactNode } from "react";
import type { FileAccess } from "../editor";
import type { DecisionAction, DecisionAttempt } from "../review";
import type { State } from "../state";
import type { View, ViewAction } from "./view";
import { panelOpen } from "./view";
import { useFunctionTask } from "./useFunctionTask";
import { TopBar } from "./TopBar";
import { AgentsPanel, AgentsRail } from "./shell/Agents";
import { FocusPanel } from "./shell/FocusPanel";
import { GraphStage } from "./shell/GraphStage";
import { LeftRail, Sidebar, ShortcutsSheet } from "./shell/Navigation";
import { BottomDock, StatusLine } from "./shell/Status";
import { useShell } from "./shell/useShell";
import "./shell/shell.css";
export interface FrameProps {
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
  /** True for a proposed change: its card offers Review change instead of the live diff. */
  canReview?: (changeId: string) => boolean;
  onReview?: (changeId: string) => void;
  /** The queue counter's click: review of the next proposed change. */
  onOpenQueue?: () => void;
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
  const { state, view, onClosePanel } = props;
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
    <div className="shell-frame flex h-full flex-col bg-background text-text">
      <TopBar state={state} onOpenQueue={props.onOpenQueue} />
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
                startAgentAction={task.action}
                maximized={shell.maximized}
                onMaximize={shell.toggleMaximize}
              />
            )}
            <GraphStage
              state={state}
              view={view}
              onNodeTap={props.onNodeTap}
              layoutKey={layoutKey}
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
      </p>
      {shell.shortcuts && (
        <ShortcutsSheet
          onClose={() => {
            shell.setShortcuts(false);
          }}
        />
      )}
    </div>
  );
}
