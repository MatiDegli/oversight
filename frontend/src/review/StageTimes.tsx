import type { ChangeStages } from "@contracts";
import "./stageTimes.css";

const stages = [
  ["agent_work", "Agent work"],
  ["checks", "Automatic checks"],
  ["waiting_for_person", "Waiting for your decision"],
  ["test_runs", "Test runs"],
] as const;

/** Floor recorded milliseconds to seconds; subsecond readings never pretend to be zero. */
export function stageDuration(ms: number): { short: string; full: string } {
  if (ms > 0 && ms < 1000) return { short: "<1s", full: "less than 1 second" };
  const seconds = Math.floor(ms / 1000);
  const parts = [
    [Math.floor(seconds / 3600), "hour", "h"],
    [Math.floor((seconds % 3600) / 60), "minute", "m"],
    [seconds % 60, "second", "s"],
  ] as const;
  const visible = parts.filter(([value]) => value > 0);
  if (seconds === 0) return { short: "0s", full: "0 seconds" };
  return {
    short: visible.map(([value, , unit]) => `${String(value)}${unit}`).join(" "),
    full: visible
      .map(([value, unit]) => `${String(value)} ${unit}${value === 1 ? "" : "s"}`)
      .join(" "),
  };
}

/** Shared metadata for review, Chat and its compact reviewing echo; never a fifth chip. */
export function StageTimes({ stages: view }: { stages: ChangeStages | null | undefined }) {
  const readings = stages.flatMap(([key, label]) => {
    const stage = view?.[key];
    if (!stage?.intervals) return [];
    if (stage.open) return [{ label, short: "running", full: "running" }];
    if (stage.ms === null) return [];
    return [{ label, ...stageDuration(stage.ms) }];
  });
  if (!readings.length) return null;
  return (
    <span role="group" aria-label="Stage times" className="stage-times">
      {readings.map(({ label, short, full }) => (
        <span key={label} role="group" aria-label={`${label}, ${full}`}>
          <span aria-hidden="true">
            {label} {short}
          </span>
        </span>
      ))}
    </span>
  );
}

export function ChangeTiming({ stages, rounds }: { stages: ChangeStages | null; rounds: number }) {
  if (stages === null) return null;
  return (
    <div className="change-timing">
      <span>
        {rounds} revision {rounds === 1 ? "round" : "rounds"}
      </span>
      <StageTimes stages={stages} />
    </div>
  );
}
