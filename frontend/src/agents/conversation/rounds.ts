import type { AgentStep } from "@contracts";
import type { State } from "../../state";

export interface RoundStep {
  kind: "step";
  seq: number;
  ts: string | undefined;
  step: AgentStep;
}
export interface Round {
  number: number;
  seq: number;
  ts: string;
  end: { seq: number; ts: string } | null;
  revision: number | null;
  outcome: "proposed" | "proposed, then you asked for changes" | "halted" | "stopped";
  paused: boolean;
  elapsedMs: number;
  toolMs: number;
  steps: RoundStep[];
}
const opens = new Set(["starting", "working", "revising"]);
const ends = new Set(["ready_for_review", "halted", "stopped", "done"]);
const ms = (ts: string) => Date.parse(ts);

/** Round clocks follow server envelopes, including pauses, never the reader's clock. */
export function selectRounds(state: State, agentId: string): Round[] {
  const rounds: Round[] = [];
  let current: Round | undefined;
  let pause: string | null = null;
  let pausedMs = 0;
  const close = (seq: number, ts: string) => {
    if (!current || current.end) return;
    if (pause !== null) pausedMs += Math.max(0, ms(ts) - ms(pause));
    pause = null;
    current.end = { seq, ts };
    current.elapsedMs = Math.max(0, ms(ts) - ms(current.ts) - pausedMs);
    current.paused = false;
  };
  for (const event of state.decisionEvents) {
    if (!("agent_id" in event.payload) || event.payload.agent_id !== agentId) continue;
    const { seq, ts } = event;
    if (event.type === "agent.status") {
      const status = event.payload.state;
      if ((!current || current.end) && opens.has(status)) {
        current = {
          number: rounds.length + 1,
          seq,
          ts,
          end: null,
          revision: null,
          outcome: "proposed",
          paused: false,
          elapsedMs: 0,
          toolMs: 0,
          steps: [],
        };
        rounds.push(current);
        pausedMs = 0;
      }
      if (!current) continue;
      if (!current.end) {
        if (pause !== null && status !== "paused") {
          pausedMs += Math.max(0, ms(ts) - ms(pause));
          pause = null;
        }
        if (status === "paused" && pause === null) pause = ts;
        current.paused = pause !== null;
        if (ends.has(status)) close(seq, ts);
      }
      if (status === "halted" || status === "stopped") current.outcome = status;
    } else if (event.type === "change.proposed" && current && !current.end) {
      current.revision = event.payload.revision;
      close(seq, ts);
    } else if (event.type === "change.resolved" && event.payload.decision === "changes_requested") {
      const round = rounds.findLast((r) => r.revision === event.payload.revision);
      if (round?.outcome === "proposed") round.outcome = "proposed, then you asked for changes";
    }
  }
  if (current && !current.end && state.clock) {
    current.elapsedMs = Math.max(
      0,
      ms(state.clock) -
        ms(current.ts) -
        pausedMs -
        (pause === null ? 0 : Math.max(0, ms(state.clock) - ms(pause))),
    );
  }
  for (const step of state.steps[agentId] ?? []) {
    const seq = state.stepSeqs[agentId]?.[step.step];
    if (seq === undefined) continue;
    const round = rounds.findLast((r) => r.seq <= seq);
    round?.steps.push({ kind: "step", seq, ts: state.stepTimes[agentId]?.[step.step], step });
    if (round) round.toolMs += step.duration_ms;
  }
  return rounds;
}
