import type { LeadEntry } from "./selectConversation";

export const unmatchedPreview = "Preview suggested; no matching lead reply is available.";
export type PreviewGroup = { entry: LeadEntry; previews: LeadEntry[]; fallback: boolean };

/** Presentation only. The retained timeline and every non-preview row keep their order. */
export function groupLeadPreviews(entries: readonly LeadEntry[]): PreviewGroup[] {
  const attached = new Map<number, LeadEntry[]>();
  const grouped = new Set<number>();
  for (const entry of entries) {
    if (entry.kind !== "lead-view" || entry.event.payload.view.kind !== "preview") continue;
    const lead = entry.event.payload.agent_id;
    const priorReply = entries.findLast(
      (e) => e.kind === "lead-reply" && e.event.payload.agent_id === lead && e.seq < entry.seq,
    );
    const message = entries.findLast((e) => e.kind === "message" && e.seq < entry.seq);
    const start = entries.findLast(
      (e) =>
        e.kind === "lead-status" &&
        e.status.agent_id === lead &&
        e.seq < entry.seq &&
        (e.status.state === "working" || e.status.state === "starting"),
    );
    const nextMessage = entries.find((e) => e.kind === "message" && e.seq > entry.seq);
    const replies = entries.filter(
      (e) =>
        e.kind === "lead-reply" &&
        e.event.payload.agent_id === lead &&
        e.seq > entry.seq &&
        e.seq < (nextMessage?.seq ?? Infinity),
    );
    const reply = replies[0];
    if (!start || reply?.kind !== "lead-reply" || replies.length !== 1) continue;
    // An idle/terminal status or a new working turn between the view and reply is a boundary.
    if (
      entries.some(
        (e) =>
          e.kind === "lead-status" &&
          e.status.agent_id === lead &&
          e.seq > entry.seq &&
          e.seq < reply.seq &&
          e.status.state !== "working",
      )
    )
      continue;
    const evidence = reply.event.payload;
    if (evidence.through_seq === null || evidence.through_seq < entry.event.payload.through_seq)
      continue;
    if (message && message.seq > (priorReply?.seq ?? 0)) {
      if (evidence.answers_seq !== message.seq || evidence.woken_by !== null) continue;
    } else {
      if (evidence.answers_seq !== null) continue;
      if (priorReply && (start.seq <= priorReply.seq || !evidence.woken_by?.length)) continue;
      if (!priorReply && evidence.woken_by !== null) continue;
    }
    attached.set(reply.seq, [...(attached.get(reply.seq) ?? []), entry]);
    grouped.add(entry.seq);
  }
  return entries
    .filter((entry) => !grouped.has(entry.seq))
    .map((entry) => ({
      entry,
      previews: attached.get(entry.seq) ?? [],
      fallback: entry.kind === "lead-view" && entry.event.payload.view.kind === "preview",
    }));
}
