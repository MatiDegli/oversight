import { parseEvent } from "./events";

export type ReplayKind = "real recorded run" | "hand-written demo fixture";
export interface ChosenReplaySource {
  text: string;
  kind: ReplayKind;
  label: string;
}

export interface RecordingProvenance {
  exporter: string;
  sha256: string;
  run_id: string;
  event_count: number;
}

export interface RecordedReplay {
  text: string;
  provenance: RecordingProvenance;
}

/** The exporter's run id records the calendar date; event timestamps may be retimed for replay. */
function recordingDate(runId: string): string {
  const iso = /^run-\d{8}-\d{6}$/.test(runId)
    ? `${runId.slice(4, 8)}-${runId.slice(8, 10)}-${runId.slice(10, 12)}`
    : "";
  const date = new Date(`${iso}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== iso)
    throw new Error("Recorded replay provenance run id has no valid date");
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

/**
 * Vite's recorded-replay loader checks the receipt's digest and every payload against the
 * Pydantic contracts before bundling. Keep the marker and real model checks here too, so
 * callers cannot label arbitrary text as a recording. Missing recordings use the fixture;
 * present but unverified recordings are refused.
 */
export function chooseReplaySource(
  recorded: string | null | undefined,
  fixture: string,
  provenance?: RecordingProvenance,
): ChosenReplaySource {
  if (recorded !== null && recorded !== undefined && recorded.trim() !== "") {
    if (
      provenance?.exporter !== "oversight.record_run.v1" ||
      !/^[a-f0-9]{64}$/.test(provenance.sha256)
    )
      throw new Error("Recorded replay requires verified exporter provenance");
    const events = recorded
      .split(/\r?\n/)
      .filter((line) => line.trim())
      .map(parseEvent);
    const first = events[0];
    if (
      first?.type !== "run.started" ||
      first.payload.run_id !== provenance.run_id ||
      events.length !== provenance.event_count ||
      events.some((event, index) => event.seq !== index + 1) ||
      first.payload.models.length === 0 ||
      events.some(
        (event) => event.type === "change.reviewed" && event.payload.reviewer_provider === "fake",
      ) ||
      first.payload.models.some((model) => model.provider === "fake" || !model.model.trim())
    )
      throw new Error("Recorded replay must be an exported real model run");
    const kind: ReplayKind = "real recorded run";
    return { text: recorded, kind, label: `Recorded run · ${recordingDate(provenance.run_id)}` };
  }
  const kind: ReplayKind = "hand-written demo fixture";
  return { text: fixture, kind, label: `Replay · ${kind}` };
}
