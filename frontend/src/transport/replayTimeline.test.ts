import { describe, expect, it } from "vitest";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "./events";
import { replayOffsets, replayPrefix } from "./replayTimeline";

const events = log.trim().split("\n").map(parseEvent);
describe("replay timeline", () => {
  it("derives the fixed compressed axis and exact prefixes", () => {
    const offsets = replayOffsets(events);
    expect(offsets.at(-1)).toBe(20178);
    expect(
      events.filter((e) => e.type === "change.proposed").map((e) => offsets[events.indexOf(e)]),
    ).toEqual([13106, 16965]);
    for (const position of [0, 8000, 15000, 20178]) {
      expect(replayPrefix(events, offsets, position)).toEqual(
        events.filter((_, i) => offsets[i]! <= position).reduce(applyEvent, createInitialState()),
      );
    }
  });
  it("groups equal timestamps, clamps negative gaps and caps long gaps", () => {
    const sample = [0, 0, -1, 10000].map((ms) => ({
      ...events[0]!,
      ts: new Date(ms).toISOString(),
    }));
    expect(replayOffsets(sample)).toEqual([0, 0, 0, 2000]);
  });
  it("rejects sequence and graph version failures", () => {
    expect(() => replayPrefix([events[1]!], [0], 0)).toThrow(/sequence or graph version/);
    const broken = [...events];
    const index = broken.findIndex((event) => event.type === "graph.delta");
    const event = broken[index]!;
    if (event.type !== "graph.delta") throw new Error("Missing delta");
    broken[index] = { ...event, payload: { ...event.payload, from_version: "missing" } };
    expect(() => replayPrefix(broken, replayOffsets(broken), Infinity)).toThrow(/graph version/);
  });
});
