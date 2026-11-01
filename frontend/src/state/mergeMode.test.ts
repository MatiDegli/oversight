import { expect, it } from "vitest";
import recorded from "../../../fixtures/events/recorded-demo.jsonl?raw";
import scripted from "../../../fixtures/events/scripted-level2.jsonl?raw";
import { applyEvent, createInitialState, createStateStore, selectMergeMode } from "./index";
import { parseEvent, startReplay } from "../transport";

it("starts unknown, reads run metadata, replaces it with events and resets on the next run", () => {
  const run = parseEvent(recorded.trim().split("\n")[0] ?? "");
  if (run.type !== "run.started") throw new Error("Missing run");
  let state = createInitialState();
  expect(selectMergeMode(state)).toBeNull();
  state = applyEvent(state, {
    ...run,
    payload: { ...run.payload, merge_mode: { mode: "ask_me", revision: 0 } },
  });
  expect(selectMergeMode(state)).toEqual({ mode: "ask_me", revision: 0 });
  state = applyEvent(state, {
    seq: 2,
    ts: run.ts,
    type: "merge_mode.changed",
    payload: { mode: "merge_when_safe", revision: 1 },
  });
  expect(selectMergeMode(state)).toEqual({ mode: "merge_when_safe", revision: 1 });
  state = applyEvent(state, {
    ...run,
    payload: { ...run.payload, run_id: "another-run", merge_mode: null },
  });
  expect(selectMergeMode(state)).toBeNull();
});

it.each([recorded, scripted])(
  "keeps absent recording metadata unknown at every sequence",
  (text) => {
    let state = createInitialState();
    for (const event of text.trim().split("\n").map(parseEvent)) {
      state = applyEvent(state, event);
      expect(selectMergeMode(state)).toBeNull();
    }
  },
);

it("projects identical mode through replay and direct reduction", async () => {
  const events = recorded.trim().split("\n").map(parseEvent).slice(0, 2);
  const last = events.at(-1);
  if (!last) throw new Error("Missing log");
  events.push({
    seq: last.seq + 1,
    ts: last.ts,
    type: "merge_mode.changed",
    payload: { mode: "merge_when_safe", revision: 1 },
  });
  const store = createStateStore();
  const modes: unknown[] = [];
  const unsubscribe = store.subscribe(({ state }) => modes.push(selectMergeMode(state)));
  const replay = startReplay({
    store,
    source: { text: events.map((e) => JSON.stringify(e)).join("\n") },
    speed: 0,
  });
  await replay.ready;
  let expected = createInitialState();
  expect(modes.slice(1)).toEqual(
    events.map((event) => {
      expected = applyEvent(expected, event);
      return selectMergeMode(expected);
    }),
  );
  replay.stop();
  unsubscribe();
});
