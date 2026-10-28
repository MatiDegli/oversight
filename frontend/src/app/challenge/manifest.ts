import type { ServerEvent } from "@contracts";
import { applyEvent, createInitialState } from "../../state";
import { parseEvent } from "../../transport";
import type { RecordedReplay, RecordingProvenance } from "../../transport";

export interface PracticeManifest {
  edition: "two-change" | "three-change";
  recording: RecordingProvenance;
  cutSeq: number;
  revealThroughSeq: number;
  cases: {
    changeId: string;
    revision: number;
    head: string;
    kind: "none";
    reference: "Approve" | "Reject";
    evidenceSeqs: number[];
    resolutionSeq: number;
  }[];
}
export const manifest: PracticeManifest = {
  edition: "two-change",
  recording: {
    exporter: "oversight.record_run.v1",
    run_id: "run-20261027-185214",
    sha256: "74407b107fd9f1d6d1ef25b8d1bb02304f170af5e43a67c034aab5e1b7ba9f51",
    event_count: 69,
  },
  cutSeq: 63,
  revealThroughSeq: 69,
  cases: [
    {
      changeId: "change-2",
      revision: 1,
      head: "611be2392dc213dfc4c15ae9cef5e1c80bd6f982",
      kind: "none",
      reference: "Approve",
      evidenceSeqs: [56],
      resolutionSeq: 67,
    },
    {
      changeId: "change-1",
      revision: 1,
      head: "cbd82aec10824a0f99541f797ace7855fb98afcf",
      kind: "none",
      reference: "Approve",
      evidenceSeqs: [62],
      resolutionSeq: 64,
    },
  ],
};

/** Input is the export already verified by Vite's recording loader, never arbitrary replay text. */
export function validateManifest(candidate: PracticeManifest, recording: RecordedReplay) {
  const fail: () => never = () => {
    throw new Error("Practice manifest does not match the verified recording");
  };
  for (const key of ["exporter", "run_id", "sha256", "event_count"] as const)
    if (candidate.recording[key] !== recording.provenance[key]) fail();
  const events = recording.text.trim().split(/\r?\n/).map(parseEvent);
  const first = events[0];
  if (
    first?.type !== "run.started" ||
    first.payload.run_id !== candidate.recording.run_id ||
    events.length !== candidate.recording.event_count ||
    events.some((event, i) => event.seq !== i + 1) ||
    !Number.isInteger(candidate.cutSeq) ||
    candidate.cutSeq < 1 ||
    candidate.cutSeq >= candidate.revealThroughSeq ||
    candidate.revealThroughSeq > events.length ||
    !Number.isInteger(candidate.revealThroughSeq) ||
    candidate.cases.length !== (candidate.edition === "two-change" ? 2 : 3) ||
    new Set(candidate.cases.map((item) => item.changeId)).size !== candidate.cases.length
  )
    fail();
  const cut = events.slice(0, candidate.cutSeq).reduce(applyEvent, createInitialState());
  if (cut.gap) fail();
  const cases = candidate.cases.map((item) => {
    const change = cut.changes[item.changeId];
    const proposal = change?.proposal;
    const resolution = events.find((event) => event.seq === item.resolutionSeq);
    if (!proposal || !resolution || resolution.type !== "change.resolved") fail();
    const matches = (event: ServerEvent) =>
      "change_id" in event.payload &&
      event.payload.change_id === item.changeId &&
      "revision" in event.payload &&
      event.payload.revision === item.revision;
    if (
      proposal.revision !== item.revision ||
      proposal.head_commit !== item.head ||
      change.resolution !== null ||
      change.status !== "ready_for_review" ||
      !["none"].includes(item.kind) ||
      !item.evidenceSeqs.length ||
      item.evidenceSeqs.some((seq) => {
        const event = events[seq - 1];
        return (
          !event ||
          seq > candidate.cutSeq ||
          event.seq !== seq ||
          !matches(event) ||
          event.type !== "change.proposed" ||
          event.payload.head_commit !== item.head
        );
      }) ||
      !matches(resolution) ||
      resolution.seq <= candidate.cutSeq ||
      resolution.seq > candidate.revealThroughSeq ||
      resolution.payload.decision !== (item.reference === "Approve" ? "approved" : "rejected") ||
      (item.reference === "Approve" && resolution.payload.merge !== "merged")
    )
      fail();
    return {
      ...item,
      proposal,
      resolution: resolution.payload,
    };
  });
  return { cut, cases, events };
}
export type Practice = ReturnType<typeof validateManifest>;
