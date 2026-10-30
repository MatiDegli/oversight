import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";
import { applyEvent, createInitialState, selectDecisionInbox } from "../../state";
import { agentSlots } from "../../theme";
import { themes } from "../../theme";
import { afterlightOf, MergeLife } from "./afterlight";
import { selectMerges } from "./life";
import { selectSignalLights } from "./signal";
import { FieldTransition } from "./transition";
import { createLampField, fieldConfig, signalConfig } from "./model";

const events = readFileSync(
  new URL("../../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
const config = fieldConfig(themes.dark, "watch");

/**
 * The recorded run waits at seq 20: the proposal opened an entry in the decisions inbox, which stays
 * open until the decision, the approval at seq 21.
 */
const awaiting = () => through(20);
const decided = () => through(21);

describe("R4 sources", () => {
  it("uses one current node at full strength and only the single supplied last read at 0.4", () => {
    const state = through(13);
    const agent = state.agents["agent-1"];
    if (!agent) throw new Error("Missing agent");
    state.agents["agent-2"] = { ...agent, agent_id: "agent-2", slot: "B" };
    state.focus["agent-2"] = {
      agent_id: "agent-2",
      mode: "read",
      ranges: [],
      node_ids: ["current-b", "not-an-emitter"],
    };
    const lights = selectSignalLights(state, { "agent-1": "last-a", "agent-2": "last-b" });
    expect(lights.map(({ id, source, weight }) => [id, source, weight])).toEqual([
      ["shop.pricing:compute_price", "agent-a", 1],
      ["current-b", "agent-b", 1],
      ["last-a", "agent-a", 0.4],
      ["last-b", "agent-b", 0.4],
    ]);
    expect(
      selectSignalLights(state, { "agent-1": "new-read" }).map((light) => light.id),
    ).not.toContain("last-a");
    expect(
      selectSignalLights(state, { "agent-1": "shop.pricing:compute_price" }).filter(
        (light) => light.source === "agent-a",
      ),
    ).toHaveLength(1);
    expect(lights.every((light) => light.steady)).toBe(true);
    // Pending nodes, cone and selected neighbors never enter this selector.
    expect(lights.some((light) => light.source === "work-light")).toBe(false);
  });

  it("keeps both agents' signals on a shared node and clears a rejected agent's light", () => {
    const state = through(13);
    const agent = state.agents["agent-1"];
    const focus = state.focus["agent-1"];
    const change = state.changes["change-1"];
    if (!agent || !focus || !change) throw new Error("Missing agent focus or change");
    state.agents["agent-2"] = { ...agent, agent_id: "agent-2", slot: "B", change_id: "change-2" };
    state.focus["agent-2"] = { ...focus, agent_id: "agent-2" };
    expect(selectSignalLights(state).map(({ id, source }) => [id, source])).toEqual([
      ["shop.pricing:compute_price", "agent-a"],
      ["shop.pricing:compute_price", "agent-b"],
    ]);
    change.status = "rejected";
    expect(selectSignalLights(state).map((light) => light.source)).toEqual(["agent-b"]);
  });

  it("lights only an agent that is acting: a ring while it works or revises, nothing otherwise", () => {
    const state = through(9);
    const agent = state.agents["agent-1"];
    if (!agent) throw new Error("Missing agent");
    for (const status of [
      "starting",
      "working",
      "revising",
      "ready_for_review",
      "paused",
      "halted",
      "done",
      "stopped",
    ] as const) {
      state.agents["agent-1"] = { ...agent, state: status };
      const acting = status === "working" || status === "revising";
      expect(
        selectSignalLights(state).map((light) => light.pulses),
        status,
      ).toEqual(acting ? [true] : []);
    }
  });

  it("holds one steady, dimmer light on the change's node while the change has an open inbox entry", () => {
    const state = awaiting();
    expect(selectDecisionInbox(state).map((entry) => entry.changeId)).toContain("change-1");
    const lights = selectSignalLights(state, { "agent-1": "shop.pricing:price_breakdown" });
    expect(lights).toEqual([
      {
        id: "shop.pricing:compute_price",
        source: "agent-a",
        weight: themes.dark["waiting-light-weight"],
        pulses: false,
        steady: true,
      },
    ]);
    // Dimmer than a working agent's, with no ring, no marker and no last read.
    expect(themes.dark["waiting-light-weight"]).toBeLessThan(themes.dark["watch-editing-weight"]);
    expect(lights[0]?.marker).toBeUndefined();
    // The same light in the day token set: the weight is a theme-independent token.
    expect(themes.day["waiting-light-weight"]).toBe(themes.dark["waiting-light-weight"]);
  });

  it("never grows, pulses or blinks however long it waits", () => {
    const first = selectSignalLights(awaiting());
    let later = awaiting();
    const tick = events.find((event) => event.type === "usage.tick");
    if (!tick) throw new Error("The fixtures changed");
    for (let k = 0; k < 40; k += 1)
      later = applyEvent(later, {
        ...tick,
        seq: 21 + k,
        ts: new Date(Date.parse("2026-10-27T14:04:00.000Z") + k * 600_000).toISOString(),
      });
    expect(later.clock).not.toBe(awaiting().clock);
    expect(selectSignalLights(later)).toEqual(first);
    expect(first.every((light) => !light.pulses && light.weight < 1)).toBe(true);
  });

  it("goes working, waiting, then to the decision's fade, with no gap in the level", () => {
    // Working: the ring at full weight, on the node the agent edits.
    expect(selectSignalLights(through(13)).map(({ weight, pulses }) => [weight, pulses])).toEqual([
      [1, true],
    ]);
    // Waiting: one steady light at the dimmer weight.
    const waiting = selectSignalLights(awaiting());
    expect(waiting.map(({ weight, pulses }) => [weight, pulses])).toEqual([
      [themes.dark["waiting-light-weight"], false],
    ]);
    // Revising after a request for changes: the working light is back.
    const revising = awaiting();
    const agent = revising.agents["agent-1"];
    if (!agent) throw new Error("Missing agent");
    revising.agents["agent-1"] = { ...agent, state: "revising" };
    expect(selectSignalLights(revising).map((light) => light.pulses)).toEqual([true]);
    // Decided: the inbox entry closes, the signal is gone, and the change's fade takes over at
    // the very weight the waiting light had.
    const state = decided();
    expect(selectDecisionInbox(state)).toEqual([]);
    expect(selectSignalLights(state)).toEqual([]);
    const [merge] = selectMerges(state);
    if (!merge) throw new Error("The decision left no fade");
    const look = afterlightOf(themes.dark);
    const life = new MergeLife(look);
    life.update([], 0, false, true);
    life.update([merge], 0, false, false, waiting);
    expect(life.lights(look.rise)[0]?.weight).toBeCloseTo(waiting[0]?.weight ?? Number.NaN, 10);
    expect(life.lights(look.rise)[0]?.id).toBe(waiting[0]?.id);
    expect(life.lights(look.delay + look.travel + look.duration / 2)[0]?.weight).toBeLessThan(
      (waiting[0]?.weight ?? 0) / 2,
    );
  });

  it("shows the waiting light after each proposal of the recorded demo, until its decision", () => {
    const demo = readFileSync(
      new URL("../../../../fixtures/events/recorded-demo.jsonl", import.meta.url),
      "utf8",
    )
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as ServerEvent);
    const lit = (seq: number) =>
      selectSignalLights(
        demo.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState()),
      )
        .filter((light) => !light.pulses)
        .map((light) => [light.id, light.weight]);
    const waiting = themes.dark["waiting-light-weight"];
    // Agent B proposes first (its change waits from seq 57), then agent A (from seq 63).
    expect(lit(57)).toContainEqual(["shop.inventory:Inventory.reserve", waiting]);
    expect(lit(63)).toEqual(
      expect.arrayContaining([
        ["shop.pricing:compute_price", waiting],
        ["shop.inventory:Inventory.reserve", waiting],
      ]),
    );
    // Agent A's change is decided at 64: only B's keeps its light, and then none at 67.
    expect(lit(64)).toEqual([["shop.inventory:Inventory.reserve", waiting]]);
    expect(lit(67)).toEqual([]);
  });

  it("leaves the field dark at rest: the recorded run, once it is merged, has no source", () => {
    expect(selectSignalLights(through(13))).not.toHaveLength(0);
    expect(selectSignalLights(through(20))).not.toHaveLength(0);
    expect(selectSignalLights(through(21))).toEqual([]);
    expect(selectSignalLights(through(23))).toEqual([]);
  });

  it("takes both colors and weights from the active tokens", () => {
    expect(
      selectSignalLights(
        through(13),
        { "agent-1": "read" },
        {
          ...themes.dark,
          "watch-editing-weight": 0.9,
          "live-diff-read-weight": 0.3,
        },
      ).map((light) => light.weight),
    ).toEqual([0.9, 0.3]);
  });
});

it.each([0.5, 1, 2])("scales the 26/72 px box spread with zoom %s", (zoom) => {
  const scaled = signalConfig(config, zoom);
  expect(scaled.profile).toEqual({ near: 26 * zoom, far: 72 * zoom, gain: config.profile.gain });
});

it.each(["watch", "live-diff", "file", "review"] as const)(
  "uses the shared spread tokens before zoom in %s",
  (mode) => {
    const custom = fieldConfig(
      { ...themes.dark, "signal-light-near": "19px", "signal-light-far": "63px" },
      mode,
    );
    expect(custom.profile).toEqual({
      near: 19,
      far: 63,
      gain: fieldConfig(themes.dark, mode).profile.gain,
    });
    expect(signalConfig(custom, 1.5).profile).toEqual({
      near: 28.5,
      far: 94.5,
      gain: custom.profile.gain,
    });
  },
);

it("fades marker and light together when the agent stops acting, stops the ring immediately, and jumps under reduced motion", () => {
  const transition = new FieldTransition({ lights: selectSignalLights(through(13)), config });
  transition.set(
    { mode: "watch", lights: selectSignalLights(through(21)) },
    config,
    0,
    "240ms",
    themes.dark["transition-easing"],
    false,
  );
  const fading = transition.sample(120).lights;
  expect(fading).toHaveLength(1);
  expect(fading[0]?.marker).toBe("agent-1");
  expect(fading[0]?.weight).toBeGreaterThan(0);
  expect(fading[0]?.weight).toBeLessThan(1);
  expect(fading[0]?.pulses).toBe(false);
  expect(transition.sample(240).lights).toEqual([]);
  transition.set(
    { mode: "watch", lights: selectSignalLights(through(13)) },
    config,
    300,
    "240ms",
    themes.dark["transition-easing"],
    true,
  );
  transition.set(
    { mode: "watch", lights: [] },
    config,
    301,
    "240ms",
    themes.dark["transition-easing"],
    true,
  );
  expect(transition.sample(301).lights).toEqual([]);
});

it("a working ring adds no ambient pool or breathing", () => {
  const lamps = createLampField();
  const light = {
    box: { x1: 140, y1: 140, x2: 280, y2: 210 },
    color: [255, 92, 31] as const,
    weight: 1,
    pulses: true,
    steady: true,
  };
  const block = (time: number) => {
    const grid = lamps(config, 500, 400, [light], time);
    return Array.from({ length: 8 }, (_, row) =>
      Array.from({ length: 8 }, (_, col) => grid.data[((20 + row) * grid.cols + 20 + col) * 4 + 3]),
    );
  };
  expect(block(0)).toEqual(block(1.45));
});

it("caps six agents at eight sources, current foci first, stable across insertion order", () => {
  const state = through(13);
  const original = state.agents["agent-1"];
  if (!original) throw new Error("Missing fixture agent");
  state.agents = {};
  state.focus = {};
  const reads: Record<string, string> = {};
  for (const slot of [...agentSlots].reverse()) {
    const id = `agent-${slot}`;
    state.agents[id] = { ...original, agent_id: id, slot };
    state.focus[id] = { agent_id: id, node_ids: [`current-${slot}`], mode: "edit", ranges: [] };
    reads[id] = `read-${slot}`;
  }
  const lights = selectSignalLights(state, reads);
  expect(themes.dark["light-source-limit"]).toBe(8);
  expect(lights).toHaveLength(8);
  expect(lights.map((light) => light.id)).toEqual([
    ...agentSlots.map((slot) => `current-${slot}`),
    "read-A",
    "read-B",
  ]);
  expect(lights.slice(0, 6).map((light) => light.source)).toEqual(
    agentSlots.map((slot) => `agent-${slot.toLowerCase()}`),
  );
  state.agents = Object.fromEntries(Object.entries(state.agents).reverse());
  expect(selectSignalLights(state, reads)).toEqual(lights);
  expect(
    selectSignalLights(state, reads, { ...themes.dark, "light-source-limit": 3 }).map(
      (light) => light.id,
    ),
  ).toEqual(["current-A", "current-B", "current-C"]);
});
