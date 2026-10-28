import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";
import { applyEvent, createInitialState } from "../../state";
import { agentSlots } from "../../theme";
import { themes } from "../../theme";
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

  it("starts and stops a work ring from agent.status, including reads and revising", () => {
    const state = through(9);
    const agent = state.agents["agent-1"];
    if (!agent) throw new Error("Missing agent");
    for (const status of [
      "working",
      "revising",
      "ready_for_review",
      "paused",
      "stopped",
    ] as const) {
      state.agents["agent-1"] = { ...agent, state: status };
      expect(selectSignalLights(state)[0]?.pulses).toBe(
        status === "working" || status === "revising",
      );
    }
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

it("fades marker and light together on approval, stops the ring immediately, and jumps under reduced motion", () => {
  const transition = new FieldTransition({ lights: selectSignalLights(through(20)), config });
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
