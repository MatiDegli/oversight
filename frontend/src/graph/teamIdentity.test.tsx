import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { themes } from "../theme";
import { combineMarks } from "./agentMarks";
import { mark } from "./marks.testSupport";
import { foldElements } from "./foldElements";
import { mapModel } from "./mapModel";
import { mapEvidence } from "./mapEvidence";
import { MapLayer } from "./MapLayer";
import { createInitialState } from "../state";
import { sameLights } from "./field/lights";
import { MilestoneLife } from "./field/milestones";
import { marksLayer } from "./layers";
import type { GraphElement } from "./elements";

const elements = (owners: ReturnType<typeof mark>[]): GraphElement[] => [
  {
    group: "nodes",
    data: { id: "module", kind: "module", title: "module.py", group: "lib" },
    classes: "module",
    position: { x: 0, y: 0 },
  },
  ...owners.map((owner, i) => ({
    group: "nodes" as const,
    data: {
      id: `node-${String(i)}`,
      parent: "module",
      kind: "function" as const,
      marks: combineMarks([owner]),
    },
    classes: "function pending marked",
    position: { x: 0, y: 100 + i * 50 },
  })),
  {
    group: "nodes",
    data: { id: "another", kind: "module", title: "another.py", group: "lib" },
    classes: "module",
    position: { x: 500, y: 0 },
  },
];
it("retains siblings and historical agents separately when folding capacity and hue reuse", () => {
  const a = mark("A", "changed", "A·1", "old"),
    b = { ...mark("A", "changed", "A·2", "new"), team: a.team };
  const g = mark("A", "changed", "G·1", "other-team");
  const folded = foldElements(elements([a, b, g]), new Set(), new Set(["module"]));
  const badges = folded.elements.find((e) => e.data.id === "module")?.data.marks?.badges;
  expect(badges?.map((b) => b.agentId)).toEqual(["old", "new", "other-team"]);
  expect(badges?.map((b) => b.label)).toEqual(["A·1", "A·2", "G·1"]);
});
it.each(["dark", "day"] as const)(
  "keeps team rings distinct and per-agent work/pending signals in %s",
  (theme) => {
    const a = mark("C", "read", "C·1"),
      b = { ...mark("C", "changed", "C·2"), team: a.team };
    const g = mark("C", "editing", "G·1");
    const drawn = elements([a, b, g]);
    const model = mapEvidence(mapModel(drawn, themes[theme]), drawn, createInitialState());
    expect(model.groups[0]?.rings?.map((r) => r.letter)).toEqual(["C", "G"]);
    expect(model.groups[0]?.modules[0]?.agents.map((a) => a.text)).toEqual([
      "C·1 reading",
      "C·2 ready",
      "G·1 editing",
    ]);
    const markup = renderToStaticMarkup(
      <MapLayer frame={{ model, level: "map", zoom: 0.2, pan: { x: 0, y: 0 } }} />,
    );
    expect(markup.match(/class="map-ring /g)).toHaveLength(2);
    expect(markup.match(/border-dashed/g)).toHaveLength(1);
    const marks = combineMarks([a, b, g]);
    if (!marks) throw new Error("Missing marks");
    const svg = decodeURIComponent(
      marksLayer(themes[theme], marks, { width: 400, height: 46 }).image,
    );
    for (const label of ["C·1", "C·2", "G·1"]) expect(svg).toContain(`>${label}</text>`);
  },
);
it("does not drop C–F marks from folded nodes", () => {
  const folded = foldElements(
    elements([mark("C"), mark("D"), mark("E"), mark("F")]),
    new Set(),
    new Set(["module"]),
  );
  expect(
    folded.elements.find((e) => e.data.id === "module")?.data.marks?.badges.map((b) => b.label),
  ).toEqual(["C", "D", "E", "F"]);
});
it("metadata changes invalidate lights even when their node and hue stay the same", () => {
  const light = {
    id: "node",
    source: "agent-a" as const,
    weight: 1,
    pulses: true,
    agentId: "one",
    team: "lead",
    label: "A",
  };
  expect(sameLights([light], [{ ...light, label: "A·1" }])).toBe(false);
  expect(sameLights([light], [{ ...light, agentId: "two" }])).toBe(false);
  expect(sameLights([light], [{ ...light, team: "other" }])).toBe(false);
});
it.each([false, true])(
  "milestones sharing hue keep independent teams; reduced motion=%s",
  (reduced) => {
    const life = new MilestoneLife(themes.dark);
    const event = {
      key: "one",
      agentId: "one",
      team: "A",
      label: "A·1",
      source: "agent-a" as const,
      id: "node",
      age: 0,
      kind: "ready" as const,
    };
    life.update(
      [event, { ...event, key: "two", agentId: "two", team: "G", label: "G·1" }],
      1000,
      reduced,
      false,
    );
    expect(life.accepted("one")).toBe(!reduced);
    expect(life.accepted("two")).toBe(!reduced);
    expect(life.gain("unrelated", 1001)).toBe(1);
    expect(life.gain("G", 1001)).toBe(1); // Its same-node collision delay is independent of hue.
    if (!reduced) expect(life.gain("A", 1001)).toBeLessThan(1);
  },
);
