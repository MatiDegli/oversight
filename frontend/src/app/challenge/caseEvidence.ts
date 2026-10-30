import type { ChangeProposed, ChangeResolved, SuiteResult } from "@contracts";
import type { Practice } from "./manifest";
import type { PracticeCase } from "./phoneEvidence";

export type EvidencePractice = Practice & { revealThroughSeq?: number };

type Part<T> = { status: "present"; value: T } | { status: "absent" };
const part = <T>(items: T[]): Part<[T, ...T[]]> =>
  items.length ? { status: "present", value: items as [T, ...T[]] } : { status: "absent" };
export interface EvidenceRun {
  seq: number;
  source: "step" | "execution" | "proposal";
  step: number | null;
  phase?: string;
  tests: SuiteResult;
}
export type History = { seq: number } & (
  { type: "proposal"; value: ChangeProposed } | { type: "resolution"; value: ChangeResolved }
);

/** Steps have no revision/head fields: bind them to the agent's proposal interval. */
export function caseEvidence(practice: EvidencePractice, entry: PracticeCase) {
  const cut = practice.cut.lastSeq;
  const proposalSeq = Math.max(...entry.evidenceSeqs);
  const before = practice.events.filter((event) => event.seq <= cut);
  const previous = before.reduce((seq, event) => {
    if (
      event.seq < proposalSeq &&
      (event.type === "change.proposed" || event.type === "change.resolved") &&
      event.payload.agent_id === entry.proposal.agent_id
    )
      return Math.max(seq, event.seq);
    return seq;
  }, 0);
  const steps: EvidenceRun[] = before.flatMap((event) =>
    event.type === "agent.step" &&
    event.payload.agent_id === entry.proposal.agent_id &&
    event.payload.tool === "run_tests" &&
    event.payload.tests &&
    event.seq > previous &&
    event.seq <= proposalSeq
      ? [{ seq: event.seq, source: "step", step: event.payload.step, tests: event.payload.tests }]
      : [],
  );
  const executions = before.flatMap((event) =>
    event.type === "tests.executed" &&
    event.payload.change_id === entry.changeId &&
    event.payload.revision === entry.revision &&
    event.payload.tested_commit === entry.head
      ? [
          {
            seq: event.seq,
            source: "execution" as const,
            step: null,
            phase: event.payload.phase,
            tests: event.payload.suite,
          },
        ]
      : [],
  );
  const matches = (value: { change_id: string; revision: number; head_commit: string }) =>
    value.change_id === entry.changeId &&
    value.revision === entry.revision &&
    value.head_commit === entry.head;
  const facts = before.flatMap((event) =>
    event.type === "change.facts_recorded" && matches(event.payload) ? [event.payload] : [],
  );
  const reviews = before.flatMap((event) =>
    event.type === "change.reviewed" && matches(event.payload) ? [event.payload] : [],
  );
  // Older callers without the validated bound can reveal only the known case resolutions.
  const reveal =
    practice.revealThroughSeq ?? Math.max(...practice.cases.map((item) => item.resolutionSeq));
  const history = practice.events.flatMap((event): History[] => {
    if (event.seq > reveal) return [];
    if (event.type === "change.proposed" && event.payload.change_id === entry.changeId)
      return [{ seq: event.seq, type: "proposal", value: event.payload }];
    if (event.type === "change.resolved" && event.payload.change_id === entry.changeId)
      return [{ seq: event.seq, type: "resolution", value: event.payload }];
    return [];
  });
  return {
    tests: part<EvidenceRun>([
      ...[...steps, ...executions].sort((a, b) => a.seq - b.seq),
      { seq: proposalSeq, source: "proposal", step: null, tests: entry.proposal.tests },
    ]),
    executions: part(executions),
    facts: part(facts),
    reviews: part(reviews),
    history: part(history),
  };
}
export function resolutionText(value: ChangeResolved) {
  const decision = {
    approved: "Approved",
    rejected: "Rejected",
    changes_requested: "Changes requested",
  }[value.decision];
  const who = {
    person: "by you",
    rules: "by rules",
    cross_review: "by cross-review",
    lead_agent: "by lead agent",
  };
  return `${decision}${value.decided_by ? ` ${who[value.decided_by]}` : ""}${value.merge ? `, ${value.merge}${value.merge_commit ? ` ${value.merge_commit.slice(0, 7)}` : ""}` : ""}${value.decided_by ? "" : " · decision maker not recorded"}`;
}
