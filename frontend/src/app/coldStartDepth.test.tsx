import { mkdir, writeFile } from "node:fs/promises";
import { expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { EffectCallback, SetStateAction } from "react";
import type { PreviewStatusEvent } from "@contracts";
import type { FrameProps } from "./Frame";
import { App } from "./App";
import { applyEvent, createInitialState, createStateStore, stateStore } from "../state";
// eslint-disable-next-line no-restricted-imports -- Measure the exact public player's production schedule.
import { createReplayPlayer } from "../transport/replayPlayer";
import { coldStartSources, coldStartStress } from "./preview/previewChat.browser.support";

const sources = await coldStartSources();
it("inventories event-bearing fixtures and verifies exporter provenance without retiming", async () => {
  expect(
    sources.map((source) => [
      source.path,
      source.events.length,
      source.bytes,
      source.beforeLeadView,
    ]),
  ).toEqual([
    ["fixtures/contracts/model-plans-1.8.json", 18, 9914, "no lead.view"],
    ["fixtures/contracts/reserved-1.3.json", 3, 4180, "no lead.view"],
    ["fixtures/contracts/reserved-1.4.json", 7, 6705, "no lead.view"],
    ["fixtures/contracts/slots-answering-1.6.json", 7, 4750, "no lead.view"],
    ["fixtures/contracts/slots-extended-1.21.json", 1, 1599, "no lead.view"],
    ["fixtures/contracts/views-1.18.json", 4, 1253, 0],
    ["fixtures/events/handwritten-behavior-review.jsonl", 24, 36038, "no lead.view"],
    ["fixtures/events/handwritten-inbox-answers.jsonl", 11, 6688, "no lead.view"],
    ["fixtures/events/handwritten-inbox-count.jsonl", 16, 11339, "no lead.view"],
    ["fixtures/events/handwritten-inbox-delegation.jsonl", 22, 13413, "no lead.view"],
    ["fixtures/events/handwritten-inbox-empty.jsonl", 2, 938, "no lead.view"],
    ["fixtures/events/handwritten-inbox-grants.jsonl", 37, 104459, "no lead.view"],
    ["fixtures/events/handwritten-inbox-kinds.jsonl", 21, 13492, "no lead.view"],
    ["fixtures/events/handwritten-inbox-revisions.jsonl", 15, 9097, "no lead.view"],
    ["fixtures/events/handwritten-lead-plans.jsonl", 11, 70209, "no lead.view"],
    ["fixtures/events/handwritten-lead-thread.jsonl", 13, 3022, "no lead.view"],
    ["fixtures/events/handwritten-lead-wakes.jsonl", 27, 75500, "no lead.view"],
    ["fixtures/events/handwritten-level1-adds-node.jsonl", 23, 34996, "no lead.view"],
    ["fixtures/events/handwritten-level1-proposed.jsonl", 20, 31483, "no lead.view"],
    ["fixtures/events/handwritten-level1.jsonl", 23, 33723, "no lead.view"],
    ["fixtures/events/handwritten-measured-cone.jsonl", 24, 40896, "no lead.view"],
    ["fixtures/events/handwritten-queue.jsonl", 5, 22782, "no lead.view"],
    ["fixtures/events/handwritten-team-colors.jsonl", 58, 25188, "no lead.view"],
    ["fixtures/events/handwritten-two-slots-braked.jsonl", 6, 22921, "no lead.view"],
    ["fixtures/events/recorded-demo.jsonl", 69, 125491, "no lead.view"],
    ["fixtures/events/scripted-level2.jsonl", 98, 167027, "no lead.view"],
    ["fixtures/events/scripted-rules.jsonl", 59, 137958, "no lead.view"],
    ["fixtures/events/stage-clocks.jsonl", 33, 41154, "no lead.view"],
    ["fixtures/views/reference-cases.json#events", 15, 21527, "no lead.view"],
  ]);
  for (const source of sources) {
    if (!source.provenance) continue;
    expect(source.provenance).toMatchObject({
      sha256: source.sha256,
      event_count: source.events.length,
      run_id: source.events[0]?.type === "run.started" ? source.events[0].payload.run_id : "",
    });
  }
  await mkdir("../.shots/cold-start-depth", { recursive: true });
  await writeFile(
    "../.shots/cold-start-depth/inventory.json",
    JSON.stringify(
      sources.map(({ events, ...source }) => ({
        ...source,
        events: events.length,
        timestamps: [events[0]?.ts, events.at(-1)?.ts],
        classification: source.complete
          ? "complete stream"
          : "contract fragments; not a runnable log",
      })),
      null,
      2,
    ) + "\n",
  );
});

it.each(sources.filter((source) => source.complete))(
  "folds $path through the public player at 1×, every seek and timestamp batch",
  async ({ events }) => {
    vi.useFakeTimers();
    const store = createStateStore();
    const cursors: number[] = [];
    store.subscribe(({ state }) => cursors.push(state.lastSeq));
    const player = createReplayPlayer({
      store,
      source: { text: events.map((event) => JSON.stringify(event)).join("\n") },
    });
    try {
      await player.ready;
      // Advance the real 40 ms schedule on a virtual clock, retaining production timestamp caps.
      await vi.advanceTimersByTimeAsync(player.getSnapshot().duration + 40);
      expect(player.getSnapshot().status).toBe("ended");
      expect(store.getState().state).toEqual(events.reduce(applyEvent, createInitialState()));
      expect(cursors.at(-1)).toBe(events.at(-1)?.seq);
      for (const event of events) {
        player.seekSeq(event.seq);
        expect(store.getState().state).toEqual(
          events.slice(0, event.seq).reduce(applyEvent, createInitialState()),
        );
      }
      for (const position of new Set(player.getSnapshot().offsets)) {
        player.seek(position);
        const eligible = events.filter(
          (_, i) => (player.getSnapshot().offsets[i] ?? Infinity) <= position,
        );
        expect(store.getState().state).toEqual(eligible.reduce(applyEvent, createInitialState()));
      }
      const settled = cursors.length;
      await vi.advanceTimersByTimeAsync(500);
      expect(cursors).toHaveLength(settled);
    } finally {
      player.dispose();
      vi.useRealTimers();
    }
  },
);

it("keeps the removed synthetic prefix contiguous with translated through_seq", () => {
  const events = coldStartStress(80);
  expect(events.map((event) => event.seq)).toEqual(Array.from({ length: 91 }, (_, i) => i + 1));
  expect(events.find((event) => event.type === "lead.view")?.payload).toMatchObject({
    through_seq: 88,
  });
  expect(events.find((event) => event.type === "lead.reply")?.payload).toMatchObject({
    through_seq: 89,
  });
  expect(events.reduce(applyEvent, createInitialState())).toMatchObject({
    lastSeq: 91,
    gap: false,
  });
});

// Isolate App's effect-to-setter edge. Keep hook values across prefix renders; Frame
// is deliberately absent, so geometry, arrivals and transport cannot cause a write.
const reconciliation = vi.hoisted(() => ({
  slots: [] as unknown[],
  index: 0,
  effects: [] as EffectCallback[],
  writes: [] as number[],
  frame: null as FrameProps | null,
}));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useState: <T,>(initial: T | (() => T)) => {
    const index = reconciliation.index++;
    if (!(index in reconciliation.slots))
      reconciliation.slots[index] =
        typeof initial === "function" ? (initial as () => T)() : initial;
    return [
      reconciliation.slots[index] as T,
      (update: SetStateAction<T>) => {
        reconciliation.writes.push(index);
        reconciliation.slots[index] =
          typeof update === "function"
            ? (update as (value: T) => T)(reconciliation.slots[index] as T)
            : update;
      },
    ];
  },
  useEffect: (effect: EffectCallback) => {
    reconciliation.effects.push(effect);
  },
}));
vi.mock("zustand", () => ({
  useStore: (
    store: typeof stateStore,
    selector: (value: ReturnType<typeof stateStore.getState>) => unknown,
  ) => selector(store.getState()),
}));
vi.mock("./Frame", () => ({
  Frame: (props: FrameProps) => {
    reconciliation.frame = props;
    return null;
  },
}));
vi.mock("./useViewHistory", () => ({ useViewHistory: () => ({ history: null, session: null }) }));
vi.mock("./challenge/usePractice", () => ({ usePractice: () => undefined }));
vi.mock("../editor/monaco", () => ({ monaco: { editor: {} } }));

function reconcileAppPass() {
  reconciliation.index = 0;
  reconciliation.effects.length = 0;
  renderToStaticMarkup(<App />);
  const effect = reconciliation.effects[0];
  if (!effect) throw new Error("Missing App reconciliation effect");
  effect();
  const frame = reconciliation.frame;
  if (!frame) throw new Error("Missing App frame");
  return frame;
}
it("keeps App's displayed state stable while preview envelopes advance open stage horizons", () => {
  const source = sources.find(({ path }) => path === "fixtures/views/reference-cases.json#events");
  if (!source) throw new Error("Missing preview reference fixture");
  reconciliation.slots.length = 0;
  reconciliation.writes.length = 0;
  const before = source.events.reduce(applyEvent, createInitialState());
  stateStore.setState({ state: before });
  try {
    expect(reconcileAppPass().state).toBe(before);
    for (const [index, phase] of ["starting", "ready", "stopped"].entries()) {
      const status: PreviewStatusEvent = {
        type: "preview.status",
        seq: before.lastSeq + index + 1,
        ts: `2026-11-03T00:00:0${String(index + 1)}.000Z`,
        payload: {
          preview_id: "preview-" + "a".repeat(32),
          run_id: before.run?.run_id ?? "",
          agent_id: "agent-1",
          change_id: "change-1",
          revision: 1,
          head_commit: "c1",
          state: phase as PreviewStatusEvent["payload"]["state"],
          reason: null,
        },
      };
      stateStore.getState().applyEvents([status]);
      const accepted = stateStore.getState().state;
      expect(accepted.gap).toBe(false);
      expect(accepted.lastSeq).toBe(status.seq);
      expect(accepted.stageEvents.at(-1)).toBe(status);
      expect(accepted.changeStages).not.toBe(before.changeStages);
      expect(accepted.changeStages["change-1"]?.agent_work).toMatchObject({
        open: true,
        ms: (index + 1) * 1000,
      });
      expect(reconcileAppPass().state).toBe(before);
    }
    const agent = source.events.find((event) => event.type === "agent.status");
    if (!agent) throw new Error("Missing agent status");
    stateStore
      .getState()
      .applyEvents([
        { ...agent, seq: stateStore.getState().state.lastSeq + 1, ts: "2026-11-03T00:00:04.000Z" },
      ]);
    expect(reconcileAppPass().state).toBe(stateStore.getState().state);
    expect(reconcileAppPass().state.stageEvents).toHaveLength(source.events.length + 4);
    stateStore.getState().applyEvents([{ ...agent, seq: stateStore.getState().state.lastSeq + 2 }]);
    expect(reconcileAppPass().state.gap).toBe(true);
    const start = source.events[0];
    if (start?.type !== "run.started") throw new Error("Missing run start");
    stateStore
      .getState()
      .applyEvents([{ ...start, payload: { ...start.payload, run_id: "new-preview-run" } }]);
    expect(reconcileAppPass().state).toBe(stateStore.getState().state);
    expect(reconcileAppPass().state.changeStages).toEqual({});
  } finally {
    stateStore.setState({ state: createInitialState() });
  }
});
it("requests no view update for unchanged cold prefixes or valid highlights, and settles one invalidation", () => {
  const events = coldStartStress(80);
  reconciliation.slots.length = 0;
  reconciliation.writes.length = 0;
  stateStore.setState({ state: createInitialState() });
  try {
    for (const event of events) {
      stateStore.getState().applyEvents([event]);
      reconcileAppPass();
    }
    expect(reconciliation.writes).toEqual([]);
    const frame = reconcileAppPass();
    frame.onViewHighlight?.(["n1"], "person-click");
    expect(reconciliation.writes).toHaveLength(1);
    reconciliation.writes.length = 0;
    const highlighted = reconcileAppPass().view;
    expect(highlighted.referenceHighlight?.nodeIds).toEqual(["n1"]);
    const status = events.find((event) => event.type === "agent.status");
    if (!status) throw new Error("Missing status");
    for (let count = 0; count < 80; count++) {
      stateStore.getState().applyEvents([{ ...status, seq: events.length + count + 1 }]);
      reconcileAppPass();
    }
    expect(reconciliation.writes).toEqual([]);
    expect(reconcileAppPass().view).toBe(highlighted);
    const state = stateStore.getState().state;
    if (!state.graph) throw new Error("Missing graph");
    stateStore.setState({ state: { ...state, graph: { ...state.graph, nodes: [] } } });
    reconcileAppPass();
    expect(reconciliation.writes).toHaveLength(1);
    const cleared = reconcileAppPass().view;
    expect(cleared).toEqual({ ...highlighted, referenceHighlight: undefined });
    for (let count = 0; count < 80; count++) {
      stateStore.getState().applyEvents([{ ...status, seq: events.length + 80 + count + 1 }]);
      reconcileAppPass();
    }
    expect(reconciliation.writes).toHaveLength(1);
    expect(reconcileAppPass().view).toBe(cleared);
  } finally {
    stateStore.setState({ state: createInitialState() });
  }
});
