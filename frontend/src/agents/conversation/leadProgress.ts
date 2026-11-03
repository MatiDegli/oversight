import type { LeadReplyEvent, LeadStatusEvent } from "@contracts";

/** A reply precedes its final status (including finish's unpublished last step). */
export function leadProgress(statuses: LeadStatusEvent[], replies: LeadReplyEvent[]) {
  let revision = 0,
    boundary = 0,
    start = 0,
    completed = 0;
  let working = false,
    awaitingFinal = false;
  let previous: string | undefined;
  for (const event of [...statuses, ...replies].sort((a, b) => a.seq - b.seq)) {
    if (event.type === "lead.reply") {
      completed++;
      working = false;
      awaitingFinal = true;
      continue;
    }
    const status = event.payload;
    if (status.revision <= revision) continue;
    revision = status.revision;
    if (status.state === "working" && previous !== "working") {
      start = boundary;
      working = true;
    }
    if (awaitingFinal && status.state !== "working" && status.state !== "starting") {
      boundary = status.step;
      awaitingFinal = false;
    }
    previous = status.state;
  }
  return { turn: completed + Number(working), start };
}
