import { ScriptedAgentLabel } from "../../teams/ScriptedAgentLabel";
import { Check, CircleStop, OctagonX } from "lucide-react";
// eslint-disable-next-line no-restricted-imports -- K2a owns this Chat-only projection, without extending the shared barrel.
import type { ChatLead } from "../../agents/chatLeadSelectors";
import { agentSlotStyle } from "../../theme";
import type { ChatAgentSelection } from "./ChatShell";
import "./chatLeads.css";

export function ChatLeadRow({
  row,
  showTeamLine = true,
  scripted = false,
  selectedAgentId,
  onOpenConversation,
}: ChatAgentSelection & { row: ChatLead; showTeamLine?: boolean; scripted?: boolean }) {
  const Row = onOpenConversation ? "button" : "div";
  const Icon =
    row.status.state === "done" ? Check : row.status.state === "halted" ? OctagonX : CircleStop;
  const attention = row.teamLine?.match(/\d+ (?:plans? )?(?:need|needs) you$/)?.[0];
  return (
    <Row
      className="chat-agent-row chat-lead-row"
      style={agentSlotStyle(row.hue)}
      data-selected={selectedAgentId === row.id}
      data-ended={row.done}
      title={[row.status.task, row.teamLine, row.activity].filter(Boolean).join(" · ")}
      aria-description={[row.teamLine, row.activity, scripted ? "Scripted demo agents" : null]
        .filter(Boolean)
        .join(" · ")}
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
            {row.done ? (
              <Icon size={12} aria-hidden="true" />
            ) : (
              <span className="chat-lead-status-dot" aria-hidden="true" />
            )}
            {row.label}
          </span>
        </span>
        {scripted && <ScriptedAgentLabel />}
        <span className="chat-agent-task">{row.status.task}</span>
        {showTeamLine && row.teamLine && (
          <span className="chat-lead-team">
            {attention ? (
              <>
                {row.teamLine.slice(0, -attention.length)}
                <strong>{attention}</strong>
              </>
            ) : (
              row.teamLine
            )}
          </span>
        )}
        <span className="chat-lead-activity" title={row.activity}>
          {row.activity}
        </span>
      </span>
    </Row>
  );
}
