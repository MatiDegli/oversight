import type { KeyboardEvent } from "react";
import type { State } from "../../state";
// eslint-disable-next-line no-restricted-imports -- C2 owns this Chat-only leaf; the shared agents barrel is outside this slice.
import { selectChatAgents } from "../../agents/chatAgentSelectors";
import { agentLook, agentSlotStyle } from "../../theme";
import type { ChatAgentSelection } from "./ChatShell";
import "./chatAgents.css";

function navigate(event: KeyboardEvent<HTMLUListElement>) {
  if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
  const rows = Array.from(event.currentTarget.querySelectorAll("button"));
  const index = rows.indexOf(event.target as HTMLButtonElement);
  if (index < 0) return;
  const next = {
    ArrowUp: Math.max(0, index - 1),
    ArrowDown: Math.min(rows.length - 1, index + 1),
    Home: 0,
    End: rows.length - 1,
  }[event.key];
  if (next === undefined) return;
  event.preventDefault();
  rows[next]?.focus();
}
export function ChatAgents({
  state,
  selectedAgentId,
  onOpenConversation,
}: ChatAgentSelection & { state: State }) {
  const rows = selectChatAgents(state);
  if (!rows.length)
    return (
      <p className="chat-agents-empty">
        No agents yet. Team hue and agent labels follow each task.
      </p>
    );
  return (
    <ul className="chat-agent-list" aria-label="Coding agents" onKeyDown={navigate}>
      {rows.map((row) => {
        const Row = onOpenConversation ? "button" : "div";
        return (
          <li key={row.id}>
            <Row
              role={onOpenConversation ? undefined : "group"}
              className="chat-agent-row"
              style={agentSlotStyle(row.hue)}
              data-agent-origin={row.id}
              data-selected={selectedAgentId === row.id}
              data-ended={row.done}
              data-review={row.ready}
              {...(onOpenConversation
                ? {
                    type: "button" as const,
                    "aria-pressed": selectedAgentId === row.id,
                    onClick: () => {
                      onOpenConversation(row.id);
                    },
                  }
                : {})}
              aria-label={`Agent ${row.letter}: ${row.task}. ${row.label}. ${row.id}`}
              aria-description={[
                row.planId ? `Plan ${row.planId}` : undefined,
                row.model,
                row.activity,
                `Step ${String(row.step)} of ${String(row.maxSteps)}`,
                row.cost,
              ]
                .filter((value) => value !== undefined)
                .join("; ")}
            >
              <span className={`chat-agent-badge ${agentLook[row.hue].tint}`} aria-hidden="true">
                {row.letter}
                <span className="chat-agent-lamp" />
              </span>
              <span className="chat-agent-content">
                <span className="chat-agent-heading">
                  <span className="chat-agent-name" title={row.planId ?? undefined}>
                    Agent {row.letter}
                  </span>
                  <span className="chat-agent-pill">{row.label}</span>
                </span>
                <span className="chat-agent-model">{row.model}</span>
                <span className="chat-agent-task">{row.task}</span>
                <span className="chat-agent-activity" title={row.activity}>
                  {row.activity}
                </span>
                <span className="chat-agent-facts">
                  <span>
                    Step <span className="font-mono">{row.step}</span> of{" "}
                    <span className="font-mono">{row.maxSteps}</span>
                  </span>
                  {row.cost !== undefined && (
                    <span className={row.cost === "Cost unknown" ? undefined : "font-mono"}>
                      {row.cost}
                    </span>
                  )}
                </span>
              </span>
            </Row>
          </li>
        );
      })}
    </ul>
  );
}
