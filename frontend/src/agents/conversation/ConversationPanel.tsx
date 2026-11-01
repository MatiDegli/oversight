import { useLayoutEffect, useRef } from "react";
import { ConversationPanelHeader } from "./ConversationPanelHeader";
import { Composer } from "./Composer";
import type { ConversationControls } from "./Composer";
import type { Conversation } from "./selectConversation";
import { TimelineEntry } from "./rows";

export interface ConversationPanelProps extends ConversationControls {
  conversation: Conversation;
  onOpenDiff?: (changeId: string) => void;
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
      className="flex h-full min-h-0 min-w-0 flex-col bg-page text-text"
    >
      <ConversationPanelHeader header={header} />
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
            <div role="listitem" key={`${entry.kind}:${String(entry.seq)}`}>
              <TimelineEntry entry={entry} changeId={header.changeId} onOpenDiff={onOpenDiff} />
            </div>
          ))}
        </div>
      </div>
      <Composer key={header.agentId} header={header} {...controls} />
    </section>
  );
}
