import { mkdir, writeFile } from "node:fs/promises";
import { expect, it, vi } from "vitest";
import { applyEvent, createInitialState, createStateStore } from "../state";
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
