import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { ChangeStages, StageTime } from "@contracts";
import { ChangeTiming, StageTimes, stageDuration } from "./StageTimes";

const time = (
  ms: number | null,
  intervals = 1,
  open = false,
  known_ms = ms ?? 5000,
): StageTime => ({ ms, intervals, open, known_ms });
const stages = (changes: Partial<ChangeStages> = {}): ChangeStages => ({
  agent_work: time(0, 0),
  checks: time(0, 0),
  waiting_for_person: time(0, 0),
  test_runs: time(0, 0),
  test_phases: [],
  problems: [],
  ...changes,
});

it.each([
  [0, "0s", "0 seconds"],
  [1, "<1s", "less than 1 second"],
  [999, "<1s", "less than 1 second"],
  [1000, "1s", "1 second"],
  [130999, "2m 10s", "2 minutes 10 seconds"],
  [3600000, "1h", "1 hour"],
  [360061000, "100h 1m 1s", "100 hours 1 minute 1 second"],
])("formats recorded %s ms with full accessible wording", (ms, short, full) => {
  expect(stageDuration(ms)).toEqual({ short, full });
});

it("renders all approved labels as neutral text with full wording and no hover dependency", () => {
  const html = renderToStaticMarkup(
    <StageTimes
      stages={stages({
        agent_work: time(1000),
        checks: time(60000),
        waiting_for_person: time(3600000),
        test_runs: time(130000),
      })}
    />,
  );
  expect(html).toContain('aria-label="Stage times"');
  for (const label of [
    "Agent work, 1 second",
    "Automatic checks, 1 minute",
    "Waiting for your decision, 1 hour",
    "Test runs, 2 minutes 10 seconds",
  ]) {
    expect(html).toContain(`aria-label="${label}"`);
  }
  expect(html).not.toContain("title=");
  expect(html).not.toContain("<li");
});

it("omits unavailable, missing and unknown stages instead of displaying the lower bound", () => {
  for (const view of [null, undefined, stages(), stages({ agent_work: time(null) })]) {
    expect(renderToStaticMarkup(<StageTimes stages={view} />)).toBe("");
  }
  expect(renderToStaticMarkup(<ChangeTiming stages={null} rounds={0} />)).toBe("");
});

it("shows genuine zero with recorded intervals and uses running only for open stages", () => {
  const html = renderToStaticMarkup(
    <StageTimes
      stages={stages({
        agent_work: time(99999, 1, true),
        checks: time(null, 1, true),
        test_runs: time(0),
      })}
    />,
  );
  expect(html).toContain('aria-label="Agent work, running"');
  expect(html).toContain('aria-label="Automatic checks, running"');
  expect(html).toContain('aria-label="Test runs, 0 seconds"');
  expect(html).not.toContain("99s");
  expect(html).not.toContain("5000");
  expect(html).not.toContain("Waiting for your decision");
});

it.each([0, 1, 2])("uses revision-round wording for %s confirmed resolutions", (rounds) => {
  expect(renderToStaticMarkup(<ChangeTiming stages={stages()} rounds={rounds} />)).toContain(
    `${String(rounds)} revision ${rounds === 1 ? "round" : "rounds"}`,
  );
});
