// eslint-disable-next-line no-restricted-imports -- This scoped selector supplies event provenance to the Chat rail.
import { isScriptedAgent } from "../../state/scriptedAgents";
import { ScriptedAgentLabel } from "../../teams/ScriptedAgentLabel";
import { ChevronDown, Plus } from "lucide-react";
import { useId, useState, type KeyboardEvent } from "react";
import type { State } from "../../state";
import { ChatLeadRow } from "./ChatLeadRow";
import { presentationFor, presentationStyle } from "../../teams/presentation";
import { AgentBadge } from "../../teams/badge";
import { chatTeamGroups, type ChatMember } from "./chatTeamGroups";
import type { ChatAgentSelection } from "./ChatShell";
import "./chatAgents.css";

function navigate(event: KeyboardEvent<HTMLUListElement>) {
  if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
  const rows = Array.from(
    event.currentTarget.querySelectorAll<HTMLButtonElement>("button.chat-agent-row"),
  ).filter((row) => !row.closest("[hidden]"));
  const index = rows.indexOf((event.target as HTMLElement).closest("button") as HTMLButtonElement);
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
  const scripted = isScriptedAgent(state, row.id);
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
      data-collision={Boolean(row.planContext)}
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
        scripted ? "Scripted demo agents" : undefined,
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
        <span className="chat-agent-model-line">
          <span className="chat-agent-model">{row.model}</span>
          {scripted && <ScriptedAgentLabel />}
        </span>
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
  const prefix = useId();
  const runId = state.run?.run_id;
  const [disclosure, setDisclosure] = useState({ runId, closed: new Set<string>() });
  if (disclosure.runId !== runId) setDisclosure({ runId, closed: new Set() });
  const closed = disclosure.runId === runId ? disclosure.closed : new Set<string>();
  const toggle = (id: string) => {
    setDisclosure((current) => {
      const next = new Set(current.closed);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { runId, closed: next };
    });
  };
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
            <div
              className="chat-team-summary"
              data-selected={
                closed.has(group.id) && group.members.some((row) => row.id === selectedAgentId)
              }
            >
              <button
                type="button"
                className="chat-team-disclosure"
                aria-label={`Members of ${group.title}`}
                aria-expanded={!closed.has(group.id)}
                aria-controls={`${prefix}-${encodeURIComponent(group.id)}`}
                onClick={() => {
                  toggle(group.id);
                }}
              >
                <ChevronDown size={16} aria-hidden="true" />
              </button>
              <span className="chat-team-ring" aria-hidden="true">
                {group.identity.letter}
              </span>
              <h3>{group.summary}</h3>
            </div>
          )}
          {group.lead && (
            <ChatLeadRow
              row={group.grouped ? { ...group.lead, teamLine: group.summary } : group.lead}
              scripted={isScriptedAgent(state, group.lead.id)}
              showTeamLine={!group.grouped}
              {...selection}
            />
          )}
          <ul
            className={group.grouped ? "chat-team-members" : "chat-lone-agent"}
            id={`${prefix}-${encodeURIComponent(group.id)}`}
            hidden={group.grouped && closed.has(group.id)}
          >
            {group.members.map((row) => (
              <li key={row.id}>
                <MemberRow row={row} member={Boolean(group.lead)} state={state} {...selection} />
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
