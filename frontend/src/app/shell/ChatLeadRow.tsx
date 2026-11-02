import { Check, CircleStop, OctagonX } from "lucide-react";
// eslint-disable-next-line no-restricted-imports -- K2a owns this Chat-only projection, without extending the shared barrel.
import type { ChatLead } from "../../agents/chatLeadSelectors";
import { agentSlotStyle } from "../../theme";
import type { ChatAgentSelection } from "./ChatShell";
import "./chatLeads.css";

export function ChatLeadRow({
  row,
  selectedAgentId,
  onOpenConversation,
}: ChatAgentSelection & { row: ChatLead }) {
  const Row = onOpenConversation ? "button" : "div";
  const Icon =
    row.status.state === "done" ? Check : row.status.state === "halted" ? OctagonX : CircleStop;
  return (
    <Row
      className="chat-agent-row chat-lead-row"
      style={agentSlotStyle(row.hue)}
      data-selected={selectedAgentId === row.id}
      data-ended={row.done}
      aria-label={`Lead ${row.letter}: ${row.status.task}. ${row.label}. ${row.id}`}
      {...(onOpenConversation
        ? {
            type: "button" as const,
            "aria-pressed": selectedAgentId === row.id,
            onClick: () => {
              onOpenConversation(row.id);
            },
          }
        : { role: "group" })}
    >
      <span className="chat-lead-ring" aria-hidden="true">
        {row.letter}
      </span>
      <span className="chat-agent-content">
        <span className="chat-agent-heading">
          <span className="chat-agent-name">Lead {row.letter}</span>
          <span className="chat-agent-pill">
            {row.done && <Icon size={12} aria-hidden="true" />}
            {row.label}
          </span>
        </span>
        <span className="chat-agent-task">{row.status.task}</span>
        {row.teamLine && <span className="chat-lead-team">{row.teamLine}</span>}
        <span className="chat-lead-activity" title={row.activity}>
          {row.activity}
        </span>
      </span>
    </Row>
  );
}
