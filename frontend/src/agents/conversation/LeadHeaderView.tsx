import { ScriptedAgentLabel } from "../../teams/ScriptedAgentLabel";
import { Check, CircleStop, OctagonX } from "lucide-react";
import { agentSlotStyle } from "../../theme";
import { costText } from "../../state";
import { leadLabels } from "../chatLeadSelectors";
import type { LeadHeader } from "./selectConversation";

export const leadNumber = (value: number) =>
  value.toLocaleString("en-US", { maximumSignificantDigits: 21 });

export function LeadHeaderView({ header }: { header: LeadHeader }) {
  const { limits } = header;
  const ended = ["stopped", "halted", "done"].includes(header.state);
  const Icon =
    header.state === "stopped"
      ? CircleStop
      : header.state === "halted"
        ? OctagonX
        : header.state === "done"
          ? Check
          : null;
  return (
    <header className="conversation-header lead-header shrink-0 border-b border-divider bg-panel px-[18px] py-4">
      <div className="flex items-start gap-[10px]">
        <span aria-hidden="true" style={agentSlotStyle(header.hue)} className="chat-lead-ring">
          {header.letter}
        </span>
        <h2 className="min-w-0 flex-1 text-[16px]! leading-[1.3]! font-semibold wrap-anywhere text-text">
          {header.task}
        </h2>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-raised px-[10px] py-[3px] text-[13px] font-semibold text-muted">
          {Icon && <Icon size={13} aria-hidden="true" />}
          {leadLabels[header.state]}
        </span>
      </div>
      {header.scripted && <ScriptedAgentLabel explain />}
      <p className="mt-2 text-[14px] text-muted wrap-anywhere">
        Lead {header.letter} · {header.modelLabel}
        {header.reasoning !== null ? ` · reasoning ${header.reasoning}` : ""}
      </p>
      <div className="lead-header-facts mt-[10px] flex flex-wrap gap-2 text-[14px] text-muted">
        <span>
          Turn {leadNumber(header.turn)}
          {limits ? ` of ${leadNumber(limits.turns)}` : ""}
        </span>
        <span>
          Plans {leadNumber(header.plans)}
          {limits ? ` of ${leadNumber(limits.plans)}` : ""}
        </span>
        <span>Step {leadNumber(header.step)}</span>
        <span>
          This turn: {leadNumber(header.turnCalls)}
          {limits ? ` of ${leadNumber(limits.tool_calls)}` : ""} tool calls
        </span>
        {header.cost !== undefined && (
          <span>{header.cost === null ? "Cost unknown" : `Cost ${costText(header.cost)}`}</span>
        )}
      </div>
      <p className="mt-2 text-[13px] leading-[1.5] text-faint wrap-anywhere">
        {limits
          ? `Limits: ${leadNumber(limits.turns)} turns · ${leadNumber(limits.tool_calls)} tool calls per turn · ${leadNumber(limits.model_calls)} model calls per turn · ${leadNumber(limits.wall_clock_s)} s per turn · ${leadNumber(limits.plans)} plans · ${leadNumber(limits.subtasks)} subtasks per plan · ${leadNumber(limits.task_chars)} characters per task.`
          : "Limits not recorded."}
      </p>
      {header.detail !== null && !ended && (
        <p className="mt-2 text-[14px] text-muted wrap-anywhere">{header.detail}</p>
      )}
    </header>
  );
}
