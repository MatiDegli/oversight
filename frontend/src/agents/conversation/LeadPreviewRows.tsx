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
  const rows = groupLeadPreviews(conversation.entries).flatMap(({ entry, previews, fallback }) => [
    { entry, fallback },
    ...previews.map((preview) => ({ entry: preview, fallback: false })),
  ]);
  // Flat keyed siblings move the existing option DOM when a late reply arrives, retaining focus.
  return rows.map(({ entry, fallback }) => (
    <Fragment key={`${entry.kind}:${String(entry.seq)}`}>
      {entry.kind === "lead-view" && entry.event.payload.view.kind === "preview" ? (
        option(entry, fallback)
      ) : (
        <LeadThreadRows
          conversation={{ ...conversation, entries: [entry] }}
          renderView={renderView}
        />
      )}
    </Fragment>
  ));
}
