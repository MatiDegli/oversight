import type { ServerEvent } from "@contracts";
import { afterEach, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/views/reference-cases.json";
import { applyEvent, createInitialState, createStateStore } from "../state";
// eslint-disable-next-line no-restricted-imports -- Exercise the production replay controller with synthetic events.
import { createReplayPlayer } from "../transport/replayPlayer";
import { graphScope } from "./graphScope";
import { reconcileReplayView } from "./replay/model";
import { reconcileReferenceHighlight, referenceHighlightIds, tabbedView } from "./view";
import { createViewHistory } from "./viewHistory";

const events = fixture.events as ServerEvent[];
const fold = (seq: number) => events.slice(0, seq).reduce(applyEvent, createInitialState());
const target = {
  ...tabbedView,
  referenceHighlight: {
    nodeIds: ["n1", "n3"],
    frameKey: "clicked",
    runId: fold(12).run?.run_id ?? "",
  },
};

afterEach(() => vi.useRealTimers());

it("keeps original references across merge publication and relights returning nodes without reframing", () => {
  const before = fold(12);
  expect(referenceHighlightIds(before, target)).toEqual(["n1", "n3"]);
  const merged = fold(13);
  const view = reconcileReferenceHighlight(merged, target);
  expect(referenceHighlightIds(merged, view)).toEqual(["n1"]);
  expect(view).toBe(target);
  const node = before.changes["change-1"]?.graph.nodes_added[0];
  if (!node || !merged.graph) throw new Error("Missing synthetic added node");
  const published = applyEvent(merged, {
    seq: 14,
    ts: events[0]?.ts ?? "",
    type: "graph.delta",
    payload: {
      from_version: merged.graph.version,
      to_version: "merged",
      commit: "merged",
      cause: "merge",
      nodes_added: [node],
      nodes_removed: [],
      nodes_changed: [],
      edges_added: [],
      edges_changed: [],
      edges_removed: [],
      parse_errors: [],
    },
  });
  expect(published.gap).toBe(false);
  expect(referenceHighlightIds(published, view)).toEqual(["n1", "n3"]);
  expect(reconcileReferenceHighlight(published, view)).toBe(target);
});

it("clears an all-missing target and never supplies an empty dimming scope before reconciliation", () => {
  const missing = {
    ...target,
    referenceHighlight: { ...target.referenceHighlight, nodeIds: ["n3"] },
  };
  const state = fold(13);
  expect(referenceHighlightIds(state, missing)).toBeNull();
  expect(graphScope(state, missing).litIds).toBeNull();
  expect(reconcileReferenceHighlight(state, missing).referenceHighlight).toBeUndefined();
});

it.each(["seek", "restart", "moment"] as const)(
  "clears an active highlight on %s and leaves later playback undimmed",
  async (action) => {
    vi.useFakeTimers();
    const store = createStateStore();
    const player = createReplayPlayer({
      store,
      source: {
        text: events
          .map((event, index) =>
            JSON.stringify({
              ...event,
              ts: new Date(Date.parse(event.ts) + index * 1000).toISOString(),
            }),
          )
          .join("\n"),
      },
    });
    await player.ready;
    player.seekSeq(12);
    expect(graphScope(store.getState().state, target).litIds).toEqual(["n1", "n3"]);
    const history = createViewHistory(player, () => store.getState().state);
    if (action === "moment") history.open(6);
    else if (action === "restart") player.restart();
    else player.seek(0);
    const state = store.getState().state;
    const next = reconcileReplayView(target, state, history.getSnapshot()?.chat);
    expect(next.referenceHighlight).toBeUndefined();
    expect(graphScope(state, next).litIds).toBeNull();
    player.seekSeq(12);
    expect(graphScope(store.getState().state, next).litIds).toBeNull();
    history.dispose();
    player.dispose();
  },
);
