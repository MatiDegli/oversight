import { useLayoutEffect, useRef, type ReactNode } from "react";
import { ConversationPanelHeader } from "./ConversationPanelHeader";
import { Composer, type ConversationControls } from "./Composer";
import { ActivityBlock } from "./ActivityBlock";
import { BudgetNotice, Notices } from "./Notices";
import { TimelineEntry } from "./rows";
import type { Conversation, ConversationEntry } from "./selectConversation";

export interface ConversationPanelProps extends ConversationControls {
  conversation: Conversation;
  renderResolution?: (entry: Extract<ConversationEntry, { kind: "resolution" }>) => ReactNode;
  onOpenDiff?: (changeId: string) => void;
}
/** The timeline owns scrolling; event arrivals never move keyboard focus. */
export function ConversationPanel({
  conversation,
  onOpenDiff,
  renderResolution,
  ...controls
}: ConversationPanelProps) {
  const { header, entries } = conversation;
  const lastActivity = entries.findLast((entry) => entry.kind === "activity");
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
      className="conversation-panel flex h-full min-h-0 w-full min-w-0 flex-col bg-page text-text"
    >
      <ConversationPanelHeader header={header} />
      <div
        ref={list}
        role="list"
        aria-label="Conversation timeline"
        tabIndex={0}
        className="conversation-timeline min-h-0 flex-1 overflow-y-auto overscroll-contain py-[9px] [overflow-anchor:none] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-text"
        onScroll={(event) => {
          const node = event.currentTarget;
          atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 2;
        }}
      >
        <div ref={content} className="conversation-entries">
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
                  showHeader={entry === lastActivity}
                />
              ) : entry.kind === "budget" ? (
                <BudgetNotice mark={entry.mark} progress={conversation.progress} />
              ) : (
                ((entry.kind === "resolution" ? renderResolution?.(entry) : null) ?? (
                  <TimelineEntry entry={entry} changeId={header.changeId} onOpenDiff={onOpenDiff} />
                ))
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
