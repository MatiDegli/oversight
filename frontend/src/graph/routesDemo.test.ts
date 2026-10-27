import { readFileSync } from "node:fs";

import type { GraphSnapshot } from "@contracts";
import cytoscape from "cytoscape";
import { expect, it } from "vitest";

import { createInitialState } from "../state";
import { themes } from "../theme";

import { graphElements } from "./elements";
import { foldElements } from "./foldElements";
import { crosses } from "./orthogonal";
import type { Route } from "./routes";
import { graphStyles } from "./styles";
import { settle } from "./updateGraph";

const graph = JSON.parse(
  readFileSync(new URL("../../../fixtures/graph/demo-repo.json", import.meta.url), "utf8"),
) as GraphSnapshot;
graph.positions = JSON.parse(
  readFileSync(new URL("../../../fixtures/layouts/demo-repo.json", import.meta.url), "utf8"),
) as GraphSnapshot["positions"];

for (const expanded of [
  new Set<string>(),
  new Set(graph.nodes.filter((n) => n.kind === "class").map((n) => n.id)),
]) {
  it(`routes every demo call without unrelated frame crossings with ${String(expanded.size)} open classes`, () => {
    const elements = foldElements(
      graphElements({ ...createInitialState(), graph }),
      expanded,
    ).elements;
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      layout: { name: "preset" },
      elements,
      style: graphStyles(themes.dark),
    });
    try {
      settle(cy);
      const frames = cy.nodes(".module, .class").toArray();
      for (const edge of cy.edges(".calls")) {
        if (edge.source().id() === edge.target().id()) continue;
        const route = edge.data("route") as Route | undefined;
        expect(route, edge.id()).toBeDefined();
        if (!route) continue;
        const from = edge.source();
        const to = edge.target();
        const start = { x: from.position("x") + route.s[0], y: from.position("y") + route.s[1] };
        const end = { x: to.position("x") + route.t[0], y: to.position("y") + route.t[1] };
        const vx = end.x - start.x;
        const vy = end.y - start.y;
        const length = Math.hypot(vx, vy);
        const line = [
          start,
          ...route.w.map((w, i) => ({
            x: start.x + w * vx - ((route.d[i] ?? 0) * vy) / length,
            y: start.y + w * vy + ((route.d[i] ?? 0) * vx) / length,
          })),
          end,
        ];
        const family = new Set([
          from.id(),
          to.id(),
          ...from.ancestors().map((n) => n.id()),
          ...to.ancestors().map((n) => n.id()),
        ]);
        for (const frame of frames.filter((f) => !family.has(f.id()))) {
          const box = frame.boundingBox({ includeLabels: false, includeOverlays: false });
          for (const [i, a] of line.entries()) {
            const b = line[i + 1];
            if (b) expect(crosses(a, b, box), `${edge.id()} crosses ${frame.id()}`).toBe(false);
          }
        }
      }
    } finally {
      cy.destroy();
    }
  });
}
