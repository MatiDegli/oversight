import { Plus, GitFork } from "lucide-react";
import { TaskBox, slotUsage, type TaskBoxProps } from "../../agents";
import type { State } from "../../state";
import { ChatAgents } from "./ChatAgents";
import type { ReactNode } from "react";
import { WhereTheyWork } from "./WhereTheyWork";

export interface ChatAgentSelection {
  selectedAgentId?: string;
  onOpenConversation?: (agentId: string) => void;
}
export interface ChatStartActions {
  leadDraft?: boolean;
  onNewTask?: () => void;
  onNewLead?: () => void;
}
export interface ChatAgentsFrameProps extends ChatAgentSelection, ChatStartActions {
  state: State;
  children?: ReactNode | ((selection: ChatAgentSelection) => ReactNode);
}
/** C2 owns the body, selection and lead position; C1 supplies only its frame. */
export function ChatAgentsFrame({
  state,
  children,
  onNewTask,
  onNewLead,
  leadDraft,
  ...selection
}: ChatAgentsFrameProps) {
  const count = slotUsage(state);
  return (
    <aside
      aria-label="Agents"
      className="chat-agents chat-side"
      data-selected-agent={selection.selectedAgentId}
    >
      <header>
        <h2>Agents</h2>
        {count && <span>{count.held} running</span>}
        <div className="chat-start-actions">
          <button type="button" className="chat-new-task" onClick={onNewTask}>
            <Plus size={15} aria-hidden="true" />
            New task
          </button>
          <button type="button" onClick={onNewLead}>
            <GitFork size={15} aria-hidden="true" />
            New lead
          </button>
        </div>
      </header>
      <div className="chat-side-body">
        {typeof children === "function" ? children(selection) : children}
        <ChatAgents state={state} leadDraft={leadDraft} {...selection} />
      </div>
    </aside>
  );
}
/** C3 owns activity and presence; an empty slot makes no claim about active agents. */
export function ChatWorkFrame({ children }: { children?: ReactNode }) {
  return (
    <aside aria-label="Where they work" className="chat-work chat-side">
      <header>
        <h2>Where they work</h2>
      </header>
      <div className="chat-side-body">{children}</div>
    </aside>
  );
}
/** Stable child positions retain the focus panel and its pending commands across layouts. */
export function ChatShell({
  state,
  active,
  rail,
  sidebar,
  agents,
  children,
  onNewTask,
  onNewLead,
  leadDraft,
  ...selection
}: ChatAgentSelection &
  ChatStartActions & {
    children: ReactNode;
    state: State;
    active: boolean;
    rail: ReactNode;
    sidebar: ReactNode;
    agents: ReactNode;
  }) {
  return (
    <div className={`shell-workspace ${active ? "chat-shell" : ""}`}>
      {rail}
      {sidebar}
      {active && (
        <ChatAgentsFrame
          state={state}
          onNewTask={onNewTask}
          onNewLead={onNewLead}
          leadDraft={leadDraft}
          {...selection}
        />
      )}
      {children}
      {!active && agents}
      {active && (
        <ChatWorkFrame>
          <WhereTheyWork />
        </ChatWorkFrame>
      )}
    </div>
  );
}

export function ChatEmpty({ onNewLead, ...props }: TaskBoxProps & ChatStartActions) {
  return (
    <div className="chat-empty">
      <div>
        <h1>What should change{props.state.run ? ` in ${props.state.run.workspace}` : ""}?</h1>
        <p className="chat-start-explanation">
          One agent does the task itself; you approve its change.
        </p>
        <TaskBox {...props} />
        <div className="chat-lead-option">
          <p>
            When work is worth splitting across several agents, a lead proposes plans you queue.
          </p>
          <button type="button" onClick={onNewLead}>
            <GitFork size={15} aria-hidden="true" />
            New lead
          </button>
        </div>
      </div>
    </div>
  );
}
