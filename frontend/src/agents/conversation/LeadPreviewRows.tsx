import { Fragment, useLayoutEffect, type ReactNode } from "react";
import LeadThreadRows from "./LeadThreadRows";
import { groupLeadPreviews, unmatchedPreview } from "./leadPreviewGroups";
import type { LeadConversation, LeadEntry } from "./selectConversation";

type ViewEntry = Extract<LeadEntry, { kind: "lead-view" }>;
/** Compose the held speech/plan renderer; no second implementation of reply semantics. */
export default function LeadPreviewRows({
  conversation,
  renderView,
  onReady,
}: {
  conversation: LeadConversation;
  renderView?: (entry: ViewEntry) => ReactNode;
  onReady?: () => void;
}) {
  useLayoutEffect(() => onReady?.(), [onReady]);
  const option = (entry: LeadEntry, fallback = false) =>
    entry.kind === "lead-view" && (
      <div role="listitem" key={`preview:${String(entry.seq)}`}>
        {fallback && <p className="mx-[22px] text-[13px] text-muted">{unmatchedPreview}</p>}
        {renderView?.(entry)}
      </div>
    );
  return groupLeadPreviews(conversation.entries).map(({ entry, previews, fallback }) => (
    <Fragment key={`${entry.kind}:${String(entry.seq)}`}>
      {fallback ? (
        option(entry, true)
      ) : (
        <LeadThreadRows
          conversation={{ ...conversation, entries: [entry] }}
          renderView={renderView}
        />
      )}
      {previews.map((preview) => option(preview))}
    </Fragment>
  ));
}
