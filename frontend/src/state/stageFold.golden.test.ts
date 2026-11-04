import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import type { ChangeStages } from "@contracts";
import { parseEvent } from "../transport";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";
import { foldStages } from "./stageFold";

const directory = new URL("../../../fixtures/events/", import.meta.url);
const golden = JSON.parse(readFileSync(new URL("stage-clocks.golden.json", directory), "utf8")) as {
  log: string;
  change_id: string;
  n: number;
  stages: ChangeStages | null;
}[];

for (const name of ["recorded-demo", "stage-clocks"]) {
  const events = readFileSync(new URL(`${name}.jsonl`, directory), "utf8")
    .trim()
    .split("\n")
    .map(parseEvent);
  const entries = golden.filter((entry) => entry.log === name);
  it(`${name}: every change and prefix matches the backend golden and shared reducer`, () => {
    const ids = [
      ...new Set(
        events.flatMap((event) =>
          "change_id" in event.payload && typeof event.payload.change_id === "string"
            ? [event.payload.change_id]
            : [],
        ),
      ),
    ].sort();
    expect(entries).toHaveLength(ids.length * events.length);
    const states = events.reduce<ReturnType<typeof createInitialState>[]>((states, event) => {
      states.push(applyEvent(states.at(-1) ?? createInitialState(), event));
      return states;
    }, []);
    for (const changeId of ids) {
      expect(
        entries.filter((entry) => entry.change_id === changeId).map((entry) => entry.n),
      ).toEqual(events.map((_, index) => index + 1));
    }
    for (const entry of entries) {
      const context = `${name}/${entry.change_id}, prefix ${String(entry.n)}`;
      expect(foldStages(events.slice(0, entry.n), entry.change_id), context).toEqual(entry.stages);
      const change = states[entry.n - 1]?.changes[entry.change_id];
      if (change) expect(change.stages, `reducer: ${context}`).toEqual(entry.stages);
    }
  });
}
