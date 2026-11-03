import { agentSlotStyle } from "../../theme";
import { EventTime, TimelineEntry } from "./rows";
import { useLayoutEffect, type ReactNode } from "react";
import type { LeadConversation } from "./selectConversation";
import { viewCardTitle } from "./LeadViewCard";

function cutText(entry: Extract<LeadConversation["entries"][number], { kind: "lead-reply" }>) {
  const { cut } = entry.event.payload;
  const n = (value: number) => value.toLocaleString("en-US", { maximumSignificantDigits: 21 });
  switch (cut) {
    case "tool_calls":
      return entry.limits
        ? `Turn ended: ${n(entry.limits.tool_calls)} tool calls reached`
        : "Turn ended: tool call limit reached";
    case "model_calls":
      return entry.limits
        ? `Turn ended: ${n(entry.limits.model_calls)} model calls reached`
        : "Turn ended: model call limit reached";
    case "time":
      return entry.limits
        ? `Turn ended: ${n(entry.limits.wall_clock_s)} s reached`
        : "Turn ended: time limit reached";
    case "spend_brake":
      return "Turn ended: the spending brake stopped the next model call";
    case null:
      return null;
  }
}

/** Speech is plain text. Server limits remain outside the lead's speech. */
export default function LeadThreadRows({
  conversation,
  onReady,
  renderView,
  renderPlan,
}: {
  conversation: LeadConversation;
  onReady?: () => void;
  renderPlan?: (planId: string) => ReactNode;
  renderView?: (
    entry: Extract<LeadConversation["entries"][number], { kind: "lead-view" }>,
  ) => ReactNode;
}) {
  useLayoutEffect(() => onReady?.(), [onReady]);
  return conversation.entries.map((entry) => {
    if (entry.kind === "lead-status") return null;
    if (entry.kind === "lead-plan") {
      if (!renderPlan) return null;
      return (
        <div role="listitem" key={`plan:${entry.planId}`}>
          {renderPlan(entry.planId)}
        </div>
      );
    }
    if (entry.kind === "lead-view") {
      if (viewCardTitle(entry.event.payload.view) === null) return null;
      return (
        <div role="listitem" key={`view:${String(entry.seq)}`}>
          {renderView?.(entry)}
        </div>
      );
    }
    return (
      <div role="listitem" key={`${entry.kind}:${String(entry.seq)}`}>
        {entry.kind !== "lead-reply" ? (
          <TimelineEntry entry={entry} changeId="" />
        ) : (
          <>
            <div className="grid grid-cols-[20px_minmax(0,1fr)] gap-x-[18px] px-[22px] py-[9px]">
              <span
                aria-hidden="true"
                style={agentSlotStyle(conversation.header.hue)}
                className="mt-0.5 inline-flex size-5 items-center justify-center rounded-full border border-(--slot-graphic) text-[11.5px] text-(--slot-text)"
              >
                {conversation.header.letter}
              </span>
              <div>
                <div className="flex justify-between gap-3 text-[13px] text-muted">
                  <strong className="font-normal">Lead {conversation.header.letter}</strong>
                  <EventTime ts={entry.event.ts} />
                </div>
                <p className="mt-[6px] text-[15px] leading-[1.55] whitespace-pre-wrap wrap-anywhere">
                  {entry.event.payload.text ??
                    "Reply withheld. Its text is not available in this source."}
                </p>
                {entry.event.payload.through_seq !== null && (
                  <p className="mt-[6px] text-[13px] text-muted">
                    As of event {entry.event.payload.through_seq}
                  </p>
                )}
              </div>
            </div>
            {entry.event.payload.cut !== null && (
              <p className="mx-[22px] my-2 rounded-[10px] border border-divider bg-inset px-[14px] py-2 text-[14px] text-muted">
                {cutText(entry)}
              </p>
            )}
          </>
        )}
      </div>
    );
  });
}
