import { readFileSync } from "node:fs";
import type { Slot } from "@contracts";
import { it, expect } from "vitest";
import { themes, agentLook, agentSlots, slotForFamily } from "./index";

// Read the contract schema as well as typing the map: a duplicated slot list cannot mask drift.
it("covers every contract Slot with one family", () => {
  const schema = JSON.parse(
    readFileSync(
      new URL("../../../contracts/generated/oversight.schema.json", import.meta.url),
      "utf8",
    ),
  ) as { $defs: { Slot: { enum: Slot[] } } };
  expect(agentSlots).toEqual(schema.$defs.Slot.enum);
  for (const slot of agentSlots) expect(slotForFamily(agentLook[slot].family)).toBe(slot);
});

it("preserves every approved A/B family value", () => {
  expect([
    themes.dark["agent-a"],
    themes.dark["agent-a-text"],
    themes.dark["agent-a-tint"],
    themes.dark["agent-a-node"],
    themes.dark["agent-a-node-new"],
  ]).toEqual([
    "#ff5c1f",
    "#ff7a45",
    { color: "#ff5c1f", alpha: 0.14 },
    { color: "#1e0c04", alpha: 0.95 },
    { color: "#140a04", alpha: 0.95 },
  ]);
  expect([
    themes.dark["agent-b"],
    themes.dark["agent-b-text"],
    themes.dark["agent-b-tint"],
    themes.dark["agent-b-node"],
    themes.dark["agent-b-node-new"],
  ]).toEqual([
    "#4f7dff",
    "#86a3ff",
    { color: "#4f7dff", alpha: 0.16 },
    { color: "#060c1e", alpha: 0.95 },
    { color: "#040814", alpha: 0.95 },
  ]);
  expect([
    themes.day["agent-a"],
    themes.day["agent-a-text"],
    themes.day["agent-a-tint"],
    themes.day["agent-a-node"],
    themes.day["agent-a-node-new"],
  ]).toEqual([
    "#b23c0a",
    "#9a3308",
    { color: "#b23c0a", alpha: 0.12 },
    { color: "#f9e7dd", alpha: 1 },
    { color: "#fbefe8", alpha: 1 },
  ]);
  expect([
    themes.day["agent-b"],
    themes.day["agent-b-text"],
    themes.day["agent-b-tint"],
    themes.day["agent-b-node"],
    themes.day["agent-b-node-new"],
  ]).toEqual([
    "#2a4fc9",
    "#2548b8",
    { color: "#2a4fc9", alpha: 0.12 },
    { color: "#e6ebfa", alpha: 1 },
    { color: "#eff2fc", alpha: 1 },
  ]);
});

it.each([
  ["C", "#a392d6", "#a392d6", "#4f3189", "#4f3189"],
  ["D", "#dba4c8", "#dba4c8", "#a2568a", "#894674"],
  ["E", "#a1c7f8", "#a1c7f8", "#3475c4", "#255ea1"],
  ["F", "#ab6e9e", "#b27ba7", "#623659", "#623659"],
] as const)(
  "keeps %s's approved graphic and text exactly",
  (slot, dark, darkText, day, dayText) => {
    const { family, text } = agentLook[slot];
    expect([themes.dark[family], themes.dark[text], themes.day[family], themes.day[text]]).toEqual([
      dark,
      darkText,
      day,
      dayText,
    ]);
  },
);

it.each([
  ["D", "#a2568a", "#f1e7eb", "#f6eff0"],
  ["E", "#3475c4", "#e4ebf2", "#eef2f4"],
] as const)(
  "preserves %s's translucent tint and original node fills",
  (slot, graphic, node, newNode) => {
    const family = agentLook[slot].family;
    expect(themes.day[`${family}-tint`]).toEqual({ color: graphic, alpha: 0.12 });
    expect(themes.day[`${family}-node`]).toEqual({ color: node, alpha: 1 });
    expect(themes.day[`${family}-node-new`]).toEqual({ color: newNode, alpha: 1 });
  },
);
