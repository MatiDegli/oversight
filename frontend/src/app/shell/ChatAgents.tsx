import { Plus } from "lucide-react";
import type { KeyboardEvent } from "react";
import type { State } from "../../state";
import { ChatLeadRow } from "./ChatLeadRow";
import { presentationFor, presentationStyle } from "../../teams/presentation";
import { AgentBadge } from "../../teams/badge";
import { chatTeamGroups, type ChatMember } from "./chatTeamGroups";
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
function MemberRow({
  row,
  member,
  state,
  selectedAgentId,
  onOpenConversation,
}: ChatAgentSelection & {
  row: ChatMember;
  member: boolean;
  state: State;
}) {
  const identity = presentationFor(state, row.id);
  const Row = onOpenConversation ? "button" : "div";
  return (
    <Row
      role={onOpenConversation ? undefined : "group"}
      className="chat-agent-row"
      style={presentationStyle(identity)}
      data-agent-origin={row.id}
      data-selected={selectedAgentId === row.id}
      data-ended={row.done}
      data-review={row.ready}
      data-member={member}
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
      <span className="chat-agent-badge" aria-hidden="true">
        <AgentBadge identity={identity} />
        <span className="chat-agent-lamp" />
      </span>
      <span className="chat-agent-content">
        <span className="chat-agent-heading">
          <span className="chat-agent-name" title={row.planId ?? undefined}>
            {member ? row.letter : `Agent ${row.letter}`}
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
  );
}

export function ChatAgents({
  state,
  selectedAgentId,
  onOpenConversation,
  leadDraft,
}: ChatAgentSelection & { state: State; leadDraft?: boolean }) {
  const groups = chatTeamGroups(state);
  const selection = { selectedAgentId, onOpenConversation };
  if (!groups.length && !leadDraft)
    return (
      <p className="chat-agents-empty">
        No agents yet. Team hue and agent labels follow each task.
      </p>
    );
  return (
    <ul className="chat-agent-list" aria-label="Agents" onKeyDown={navigate}>
      {groups.map((group) => (
        <li
          key={group.id}
          className={group.grouped ? "chat-team" : undefined}
          style={presentationStyle(group.identity)}
        >
          {group.grouped && (
            <div className="chat-team-summary">
              <span className="chat-team-ring" aria-hidden="true">
                {group.identity.letter}
              </span>
              <h3>{group.summary}</h3>
            </div>
          )}
          {group.lead && <ChatLeadRow row={group.lead} {...selection} />}
          <ul className={group.grouped ? "chat-team-members" : "chat-lone-agent"}>
            {group.members.map((row) => (
              <li key={row.id}>
                <MemberRow row={row} member={group.grouped} state={state} {...selection} />
              </li>
            ))}
          </ul>
        </li>
      ))}
      {leadDraft && (
        <li>
          <div
            className="chat-agent-row chat-lead-draft"
            data-selected="true"
            role="group"
            aria-label="New lead draft"
          >
            <span className="chat-lead-draft-icon">
              <Plus size={18} aria-hidden="true" />
            </span>
            <span className="chat-agent-content">
              <span className="chat-agent-heading">
                <span className="chat-agent-name">New lead</span>
                <span className="chat-agent-pill">Not started</span>
              </span>
              <span className="chat-lead-team">Writing its first task</span>
            </span>
          </div>
        </li>
      )}
    </ul>
  );
}
