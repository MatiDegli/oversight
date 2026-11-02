import { TaskBox, type TaskBoxProps } from "../../agents";
import type { ReactNode } from "react";
import { WhereTheyWork } from "./WhereTheyWork";

export interface ChatAgentSelection {
  selectedAgentId?: string;
  onOpenConversation?: (agentId: string) => void;
}
export interface ChatAgentsFrameProps extends ChatAgentSelection {
  children?: ReactNode | ((selection: ChatAgentSelection) => ReactNode);
}
/** C2 owns the body, selection and lead position; C1 supplies only its frame. */
export function ChatAgentsFrame({ children, ...selection }: ChatAgentsFrameProps) {
  return (
    <aside
      aria-label="Agents"
      className="chat-agents chat-side"
      data-selected-agent={selection.selectedAgentId}
    >
      <header>
        <h2>Agents</h2>
      </header>
      <div className="chat-side-body">
        {typeof children === "function" ? children(selection) : children}
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
  active,
  rail,
  sidebar,
  agents,
  children,
  ...selection
}: ChatAgentSelection & {
  children: ReactNode;
  active: boolean;
  rail: ReactNode;
  sidebar: ReactNode;
  agents: ReactNode;
}) {
  return (
    <div className={`shell-workspace ${active ? "chat-shell" : ""}`}>
      {rail}
      {sidebar}
      {active && <ChatAgentsFrame {...selection} />}
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

export function ChatEmpty(props: TaskBoxProps) {
  return (
    <div className="chat-empty">
      <div>
        <h1>What should change{props.state.run ? ` in ${props.state.run.workspace}` : ""}?</h1>
        <TaskBox {...props} />
      </div>
    </div>
  );
}
