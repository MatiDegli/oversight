import { expect, it } from "vitest";
import { fakeCandidate } from "./previewFake";
import { initialOpening, previewOpening } from "./previewOpening";
import type {
  OpeningConnection,
  OpeningGuards,
  OpeningInput,
  OpeningState,
  PreviewSurface,
} from "./previewOpening";

const connection: OpeningConnection = { run: fakeCandidate.run_id, source: "live", generation: 1 };
const guards: OpeningGuards = {
  surface: "chat-wide",
  configured: true,
  preference: true,
  targetVisible: true,
  allocatedSlot: true,
  openReview: false,
  openPreview: false,
  layoutUnchanged: true,
  tabUnchanged: true,
  framingUnchanged: true,
  focusUnchanged: true,
  scrollUnchanged: true,
};
function fold(inputs: OpeningInput[], state = initialOpening) {
  for (const input of inputs) state = previewOpening(state, input).state;
  return state;
}
function reset() {
  return previewOpening(initialOpening, { type: "reset", connection }).state;
}
function armed() {
  return fold(
    [
      { type: "fence", connection, seq: 10, committed: true },
      { type: "prefix", connection, seq: 11, contiguous: true },
    ],
    reset(),
  );
}
function finish(patch: Partial<Extract<OpeningInput, { type: "finish" }>> = {}): OpeningInput {
  return {
    type: "finish",
    connection,
    seq: 11,
    live: true,
    genuinelyNew: true,
    candidate: fakeCandidate,
    guards,
    ...patch,
  };
}

it("disarms load, arms only after the accepted prefix catches up", () => {
  let state = reset();
  expect(state.armed).toBe(false);
  state = previewOpening(state, { type: "fence", connection, seq: 10, committed: true }).state;
  state = previewOpening(state, { type: "prefix", connection, seq: 9, contiguous: true }).state;
  expect(state.armed).toBe(false);
  state = previewOpening(state, { type: "prefix", connection, seq: 10, contiguous: true }).state;
  expect(state.armed).toBe(true);
});
it.each([0, 9, 10])("suppresses finishes inclusively through fence: %i", (seq) => {
  expect(previewOpening(armed(), finish({ seq })).action).toBeNull();
});
it("consumes disarmed finishes without replay on catch-up", () => {
  const consumed = previewOpening(reset(), finish()).state;
  const state = fold(
    [
      { type: "fence", connection, seq: 10, committed: true },
      { type: "prefix", connection, seq: 11, contiguous: true },
    ],
    consumed,
  );
  expect(previewOpening(state, finish()).action).toBeNull();
});
it.each([
  { ...connection, generation: 0 },
  { ...connection, run: "wrong" },
  { ...connection, source: "replay" },
])("rejects stale or wrong fence $source/$run/$generation", (stale) => {
  const state = previewOpening(reset(), {
    type: "fence",
    connection: stale,
    seq: 10,
    committed: true,
  }).state;
  expect(state).toEqual(reset());
});
it("rejects uncommitted, non-integer and repeated fences", () => {
  for (const input of [
    { seq: 10, committed: false },
    { seq: -1, committed: true },
    { seq: 1.5, committed: true },
  ])
    expect(previewOpening(reset(), { type: "fence", connection, ...input }).state.fence).toBeNull();
  expect(
    previewOpening(armed(), { type: "fence", connection, seq: 1, committed: true }).state.fence,
  ).toBe(10);
});
it.each(["reconnect", "run", "source", "uncertain", "gap"])("disarms on %s", (kind) => {
  const state =
    kind === "gap"
      ? previewOpening(armed(), { type: "gap" }).state
      : previewOpening(armed(), {
          type: "reset",
          connection:
            kind === "uncertain"
              ? null
              : {
                  ...connection,
                  generation: 2,
                  run: kind === "run" ? "new" : connection.run,
                  source: kind === "source" ? "replay" : connection.source,
                },
        }).state;
  expect(state.armed).toBe(false);
  expect(state.fence).toBeNull();
  expect(previewOpening(state, finish()).action).toBeNull();
});
it("old onopen/ready cannot arm: no such input exists; a fresh generation requires its own fence", () => {
  const fresh = { ...connection, generation: 2 };
  let state = previewOpening(armed(), { type: "reset", connection: fresh }).state;
  state = previewOpening(state, {
    type: "prefix",
    connection: fresh,
    seq: 20,
    contiguous: true,
  }).state;
  state = previewOpening(state, { type: "fence", connection, seq: 10, committed: true }).state;
  expect(state.armed).toBe(false);
  state = previewOpening(state, {
    type: "fence",
    connection: fresh,
    seq: 20,
    committed: true,
  }).state;
  expect(state.armed).toBe(true);
});
it.each([false, true])("disarms gaps and regressive accepted prefix (%s)", (contiguous) => {
  expect(
    previewOpening(armed(), { type: "prefix", connection, seq: 5, contiguous }).state.armed,
  ).toBe(false);
});

it("deduplicates by run/agent/revision/head and admits a genuinely new finish", () => {
  const first = previewOpening(armed(), finish());
  expect(first.action).toBe("open_preview");
  expect(previewOpening(first.state, finish()).action).toBeNull();
  for (const patch of [{ agent_id: "other" }, { revision: 2 }, { head_commit: "other" }])
    expect(
      previewOpening(first.state, finish({ candidate: { ...fakeCandidate, ...patch } })).action,
    ).toBe("open_preview");
});
it.each([
  { live: false },
  { genuinelyNew: false },
  { seq: 12 },
  { candidate: { ...fakeCandidate, run_id: "wrong" } },
  { connection: { ...connection, generation: 2 } },
])("rejects replay, historical, unaccepted or uncertain identity: %j", (patch) => {
  expect(previewOpening(armed(), finish(patch)).action).toBeNull();
});
const blocked: Partial<OpeningGuards>[] = [
  { configured: false },
  { preference: false },
  { targetVisible: false },
  { allocatedSlot: false },
  { openReview: true },
  { openPreview: true },
  { layoutUnchanged: false },
  { tabUnchanged: false },
  { framingUnchanged: false },
  { focusUnchanged: false },
  { scrollUnchanged: false },
];
it.each(blocked)("falls back to See preview once for %j, with no delayed retry", (patch) => {
  const result = previewOpening(armed(), finish({ guards: { ...guards, ...patch } }));
  expect(result.action).toBe("see_preview");
  expect(previewOpening(result.state, finish()).action).toBeNull();
});
const surfaces: PreviewSurface[] = [
  "chat-wide",
  "chat-narrow",
  "oversight-watch",
  "oversight-focus",
  "usage",
  "settings",
  "inbox",
];
it.each(surfaces)("does not invent an allocated slot or mutate %s", (surface) => {
  const input = finish({ guards: { ...guards, surface, allocatedSlot: false } });
  const before = structuredClone(input),
    state: OpeningState = armed(),
    saved = structuredClone(state);
  expect(previewOpening(state, input).action).toBe("see_preview");
  expect(input).toEqual(before);
  expect(state).toEqual(saved);
});
