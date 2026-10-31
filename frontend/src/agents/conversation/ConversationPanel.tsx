import { useLayoutEffect, useRef } from "react";
import { agentLook } from "../../theme";
import { costText } from "../../state";
import { labels } from "../AgentCard";
import { Composer } from "./Composer";
import type { ConversationControls } from "./Composer";
import type { Conversation, ConversationHeader } from "./selectConversation";
import { ActivityBlock } from "./ActivityBlock";
import { BudgetNotice, Notices } from "./Notices";
import { TimelineEntry } from "./rows";

export interface ConversationPanelProps extends ConversationControls {
  conversation: Conversation;
  onOpenDiff?: (changeId: string) => void;
}
const chip =
  "max-w-full rounded-control border border-border px-[10px] py-1 text-[14px] wrap-anywhere";

function Header({ header }: { header: ConversationHeader }) {
  return (
    <header className="shrink-0 border-b border-divider bg-panel px-[18px] py-4">
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

/** The timeline owns scrolling; event arrivals never move keyboard focus. */
export function ConversationPanel({
  conversation,
  onOpenDiff,
  ...controls
}: ConversationPanelProps) {
  const { header, entries } = conversation;
  const list = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  useLayoutEffect(() => {
    const node = list.current;
    const body = content.current;
    if (!node || !body) return;
    // Disclosure and panel geometry can change without a scroll event.
    const observer = new ResizeObserver(() => {
      atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 2;
    });
    observer.observe(node);
    observer.observe(body);
    return () => {
      observer.disconnect();
    };
  }, []);
  useLayoutEffect(() => {
    if (atBottom.current && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [entries]);
  return (
    <section
      aria-label={`Agent ${header.slot} conversation`}
      className="flex h-full min-h-0 w-full min-w-0 flex-col bg-page text-text"
    >
      <Header header={header} />
      <div
        ref={list}
        role="list"
        aria-label="Conversation timeline"
        tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain py-[9px] [overflow-anchor:none] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-text"
        onScroll={(event) => {
          const node = event.currentTarget;
          atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 2;
        }}
      >
        <div ref={content}>
          {entries.map((entry) => (
            <div
              role="listitem"
              key={`${entry.kind === "activity" ? "round" : entry.kind}:${String(entry.seq)}`}
            >
              {entry.kind === "activity" || entry.kind === "round" ? (
                <ActivityBlock
                  entry={entry}
                  slot={header.slot}
                  changeId={header.changeId}
                  onOpenDiff={onOpenDiff}
                />
              ) : entry.kind === "budget" ? (
                <BudgetNotice mark={entry.mark} progress={conversation.progress} />
              ) : (
                <TimelineEntry entry={entry} changeId={header.changeId} onOpenDiff={onOpenDiff} />
              )}
            </div>
          ))}
          <Notices conversation={conversation} />
        </div>
      </div>
      <Composer key={header.agentId} header={header} {...controls} />
    </section>
  );
}
