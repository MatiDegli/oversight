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
  it("labels a recorded log as a real recorded run, with its model and date", () => {
    const source = chooseReplaySource(recorded, fixture, provenance);
    expect(source.text).toBe(recorded);
    expect(source.kind).toBe("real recorded run");
    expect(source.label).toBe(
      "Replay · real recorded run · muse-spark-1.3-contributor · 2026-10-27",
    );
  });

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

it("refuses fake reviews inside a real model recording", () => {
  const rows = recorded.trim().split("\n");
  rows.push(
    JSON.stringify({
      seq: rows.length + 1,
      ts: "2026-10-27T14:03:40.000Z",
      type: "change.reviewed",
      payload: { reviewer_provider: "fake" },
    }),
  );
  expect(() =>
    chooseReplaySource(rows.join("\n"), fixture, { ...provenance, event_count: rows.length }),
  ).toThrow();
});
