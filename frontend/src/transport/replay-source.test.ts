import { describe, expect, it } from "vitest";

import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { chooseReplaySource } from "./replay-source";

const recorded = fixture.replace('"model":"muse-spark"', '"model":"muse-spark-1.3-contributor"');
const provenance = {
  exporter: "oversight.record_run.v1",
  sha256: "a".repeat(64),
  run_id: "run-20261027-140312",
  event_count: recorded.trim().split("\n").length,
};

describe("chooseReplaySource", () => {
  it("labels a recorded log with the date from its verified provenance run id", () => {
    const source = chooseReplaySource(recorded, fixture, provenance);
    expect(source.text).toBe(recorded);
    expect(source.kind).toBe("real recorded run");
    expect(source.label).toBe("Recorded run · Oct 27, 2026");
  });

  it("uses the provenance date even when the first event has a different date", () => {
    const laterEvents = recorded.replaceAll("2026-10-27T", "2026-10-28T");
    expect(chooseReplaySource(laterEvents, fixture, provenance).label).toBe(
      "Recorded run · Oct 27, 2026",
    );
  });

  it.each(["run-undated", "run-20260230-140312", "run-20261327-140312"])(
    "refuses an invalid provenance date: %s",
    (runId) => {
      const text = recorded.replace(provenance.run_id, runId);
      expect(() => chooseReplaySource(text, fixture, { ...provenance, run_id: runId })).toThrow(
        "provenance run id has no valid date",
      );
    },
  );

  it.each([fixture, "not JSON", '{"not":"an event"}'])(
    "refuses unverified text instead of calling it a recording",
    (text) => {
      expect(() => chooseReplaySource(text, fixture)).toThrow();
    },
  );

  it("refuses a fake model even with an exporter marker", () => {
    const fake = recorded.replace('"provider":"meta"', '"provider":"fake"');
    expect(() => chooseReplaySource(fake, fixture, provenance)).toThrow();
  });

  it("refuses invalid JSON on a later line even with an exporter marker", () => {
    expect(() => chooseReplaySource(recorded + "\nnot JSON", fixture, provenance)).toThrow();
  });

  it("refuses an exporter marker for another run", () => {
    expect(() =>
      chooseReplaySource(recorded, fixture, { ...provenance, run_id: "run-20261028-000000" }),
    ).toThrow();
  });

  it("labels the hand-written log as a fixture when no recorded log exists", () => {
    for (const missing of [undefined, null, ""]) {
      const source = chooseReplaySource(missing, fixture);
      expect(source.text).toBe(fixture);
      expect(source.kind).toBe("hand-written demo fixture");
      expect(source.label).toBe("Replay · hand-written demo fixture");
    }
  });
});
