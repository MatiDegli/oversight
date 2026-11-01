import { agentLook } from "../../theme";
import { costText } from "../../state";
import { labels } from "../AgentCard";
import type { ConversationHeader } from "./selectConversation";

const chip =
  "max-w-full rounded-control border border-border px-[10px] py-1 text-[14px] wrap-anywhere";

export function ConversationPanelHeader({ header }: { header: ConversationHeader }) {
  return (
    <header className="conversation-header shrink-0 border-b border-divider bg-panel px-[18px] py-4">
      <div className="flex items-start gap-[10px]">
        <span
          className={`flex size-(--letter-badge-size) shrink-0 items-center justify-center rounded-label font-bold ${agentLook[header.slot].swatch}`}
        >
          {header.slot}
        </span>
        <h2 className="min-w-0 text-[20px] leading-[1.3] font-bold wrap-anywhere">{header.task}</h2>
        <span className="shrink-0 rounded-full bg-raised px-[10px] py-[3px] text-[13px] font-semibold text-muted">
          {header.waiting ? "Waiting" : labels[header.state]}
        </span>
      </div>
      <div className="mt-[14px] flex flex-wrap gap-2">
        <span className={chip}>
          {header.modelLabel}
          {header.reasoning !== null ? ` · reasoning ${header.reasoning}` : ""}
        </span>
        <span className={chip}>
          Step <span className="font-mono">{Math.min(header.step, header.maxSteps)}</span> of{" "}
          <span className="font-mono">{header.maxSteps}</span>
        </span>
        {header.cost !== undefined && (
          <span className={`${chip} ${header.cost === null ? "" : "font-mono"}`}>
            {header.cost === null ? "Cost unknown" : costText(header.cost)}
          </span>
        )}
        {header.round > 1 && <span className={chip}>Round {header.round}</span>}
        {header.zonePaths[0] && (
          <span className={chip}>
            Zone: {header.zonePaths[0]}
            {header.zonePaths.length > 1 ? ` and ${String(header.zonePaths.length - 1)} more` : ""}
          </span>
        )}
      </div>
    </header>
  );
}
