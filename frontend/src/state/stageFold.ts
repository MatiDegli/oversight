import type {
  ChangeResolved,
  ChangeStages,
  ServerEvent,
  StageTime,
  SuiteExecution,
} from "@contracts";

const phases = ["agent", "proposal", "combined", "base_probe", "head_probe"] as const;
type Phase = (typeof phases)[number];
type Point = { seq: number; ms: number | null };
type Window = { revision: number; start: Point; receipt: Point | null; combined: number | null };

/** Match parse_ts: UTC, fractional seconds, valid calendar date; floor to milliseconds. */
function point(event: ServerEvent): Point {
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{1,6})Z$/.exec(event.ts);
  const normalized = match
    ? `${match[1] ?? ""}.${match[2]?.padEnd(3, "0").slice(0, 3) ?? ""}Z`
    : "";
  const ms = Date.parse(normalized);
  const valid =
    !normalized.startsWith("0000") &&
    Number.isFinite(ms) &&
    new Date(ms).toISOString() === normalized;
  return { seq: event.seq, ms: valid ? ms : null };
}

class Stage {
  known_ms = 0;
  intervals = 0;
  unknown = false;
  running = false;

  add(ms: number | null, running = false) {
    this.intervals++;
    this.unknown ||= ms === null;
    this.known_ms += ms ?? 0;
    this.running ||= running;
  }

  view(): StageTime {
    return {
      ms: this.unknown ? null : this.known_ms,
      known_ms: this.known_ms,
      intervals: this.intervals,
      open: this.running,
    };
  }
}

class Fold {
  agent = new Stage();
  checks = new Stage();
  waiting = new Stage();
  tests = new Stage();
  phases = new Map<Phase, { ms: number; runs: number }>();
  problems: { seq: number; sentence: string }[] = [];
  active: Point | null = null;
  window: Window | null = null;

  problem(seq: number, sentence: string) {
    this.problems.push({ seq, sentence });
  }

  elapsed(start: Point, end: Point): number | null {
    if (start.ms === null || end.ms === null || end.ms < start.ms) {
      const seq = start.ms === null ? start.seq : end.seq;
      this.problem(seq, `Timestamp goes back or is invalid at seq ${String(seq)}.`);
      return null;
    }
    return end.ms - start.ms;
  }

  status(state: string, at: Point) {
    if (["starting", "working", "revising"].includes(state)) {
      this.active ??= at;
    } else if (this.active !== null) {
      this.agent.add(this.elapsed(this.active, at));
      this.active = null;
    }
  }

  suite(payload: SuiteExecution, at: Point) {
    const duration = payload.suite.duration_ms;
    if (duration < 0) this.problem(at.seq, `Negative suite duration at seq ${String(at.seq)}.`);
    this.tests.add(duration >= 0 ? duration : null);
    const phase = this.phases.get(payload.phase) ?? { ms: 0, runs: 0 };
    this.phases.set(payload.phase, {
      ms: phase.ms + (duration >= 0 ? duration : 0),
      runs: phase.runs + 1,
    });
    if (this.window?.revision === payload.revision && payload.phase === "combined") {
      this.window.combined = duration;
    }
  }

  closeWindow(end: Point, reason: "resolved" | "replaced" | "open", resolution?: ChangeResolved) {
    const window = this.window;
    if (window === null) return;
    let start: Point;
    if (window.receipt !== null) {
      this.checks.add(this.elapsed(window.start, window.receipt));
      start = window.receipt;
    } else if (reason === "open") {
      this.checks.add(this.elapsed(window.start, end), true);
      return;
    } else {
      start = window.start;
    }
    if (reason === "replaced") {
      this.waiting.add(null);
      this.problem(
        end.seq,
        `Revision ${String(window.revision)} replaced without a decision at seq ${String(end.seq)}.`,
      );
    } else if (reason === "open") {
      this.waiting.add(this.elapsed(start, end), true);
    } else if (resolution) {
      this.decision(window, start, end, resolution);
    }
    this.window = null;
  }

  decision(window: Window, start: Point, end: Point, resolution: ChangeResolved) {
    if (resolution.decided_by === null) {
      this.waiting.add(null);
      this.problem(
        end.seq,
        `No recorded decider for revision ${String(window.revision)} at seq ${String(end.seq)}.`,
      );
      return;
    }
    if (resolution.decided_by !== "person") return;
    let elapsed = this.elapsed(start, end);
    if (elapsed !== null && resolution.decision === "approved" && window.combined !== null) {
      const duration = window.combined;
      if (duration < 0) {
        elapsed = null;
        this.problem(
          end.seq,
          `Waiting unknown after negative combined suite at seq ${String(end.seq)}.`,
        );
      } else if (duration > elapsed) {
        elapsed = null;
        this.problem(end.seq, `Combined run longer than the wait at seq ${String(end.seq)}.`);
      } else {
        elapsed -= duration;
      }
    }
    this.waiting.add(elapsed);
  }

  consume(event: ServerEvent, at: Point) {
    // Other envelopes contribute continuity and the horizon only, as in the backend fold.
    // eslint-disable-next-line @typescript-eslint/switch-exhaustiveness-check
    switch (event.type) {
      case "agent.status":
        this.status(event.payload.state, at);
        break;
      case "change.proposed":
        this.closeWindow(at, "replaced");
        this.window = {
          revision: event.payload.revision,
          start: at,
          receipt: null,
          combined: null,
        };
        break;
      case "change.resolved":
        if (this.window?.revision === event.payload.revision)
          this.closeWindow(at, "resolved", event.payload);
        break;
      case "change.facts_recorded":
      case "change.reviewed":
      case "change.assessed":
        if (this.window?.revision === event.payload.revision) this.window.receipt = at;
        break;
      case "tests.executed":
        this.suite(event.payload, at);
        break;
      default:
        break;
    }
  }

  finish(end: Point): ChangeStages {
    if (this.active !== null) this.agent.add(this.elapsed(this.active, end), true);
    this.closeWindow(end, "open");
    return {
      agent_work: this.agent.view(),
      checks: this.checks.view(),
      waiting_for_person: this.waiting.view(),
      test_runs: this.tests.view(),
      test_phases: phases.flatMap((phase) => {
        const counts = this.phases.get(phase);
        return counts ? [{ phase, ...counts }] : [];
      }),
      problems: this.problems
        .sort((a, b) => a.seq - b.seq)
        .slice(0, 20)
        .map((p) => p.sentence),
    };
  }
}

/** Backend stage_fold parity: complete committed prefix, including unrelated envelopes.
 * Approval waiting ends at the receipt less the combined suite; it may include merge queueing.
 * Stages overlap. No wall clock or playback position participates in this projection.
 */
export function foldStages(events: readonly ServerEvent[], changeId: string): ChangeStages | null {
  if (!events.length) return null;
  const fold = new Fold();
  let at: Point = { seq: 0, ms: null };
  for (const [index, event] of events.entries()) {
    if (!Number.isInteger(event.seq) || event.seq !== index + 1) return null;
    at = point(event);
    if ("change_id" in event.payload && event.payload.change_id === changeId) {
      fold.consume(event, at);
    }
  }
  return fold.finish(at);
}
