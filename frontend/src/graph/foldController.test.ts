import cytoscape from "cytoscape";
import { afterEach, expect, it, vi } from "vitest";

import { createInitialState } from "../state";
import { themes } from "../theme";

import type { GraphElement } from "./elements";
import { foldController } from "./foldController";
import { applyRoutes, dragRoutes } from "./routes";
import type { Route } from "./routes";
import { graphStyles } from "./styles";

const full: GraphElement[] = [
  ...["left", "module", "right"].map((id, i): GraphElement => ({
    group: "nodes",
    data: { id, kind: "module", label: id },
    classes: "module",
    position: { x: i * 600, y: 200 },
  })),
  {
    group: "nodes",
    data: { id: "child", kind: "function", parent: "module", label: "child" },
    classes: "function",
    position: { x: 600, y: 240 },
  },
  ...[
    ["left", "child"],
    ["child", "right"],
  ].map(([source, target]): GraphElement => ({
    group: "edges",
    data: { id: `${source ?? ""}>${target ?? ""}`, kind: "calls", source, target },
    classes: "calls exact",
  })),
];

afterEach(() => vi.unstubAllGlobals());

for (const reduced of [false, true]) {
  it.each([false, true])(
    `routes the first module collapse at its final position (drag=%s, reduced=${String(reduced)})`,
    (drag) => {
      vi.stubGlobal("matchMedia", () => ({ matches: reduced }));
      const frame = vi.fn();
      vi.stubGlobal("requestAnimationFrame", frame);
      const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
      try {
        const folding = foldController(cy);
        const state = createInitialState();
        const render = () => folding.render(state, full);
        const toggle = () => {
          expect(folding.toggle("module")).toBe(true);
          expect(render()).toBe(true);
        };
        render();
        const module = cy.getElementById("module");
        const original = { ...module.position() };
        const camera = { zoom: cy.zoom(), pan: { ...cy.pan() } };
        toggle();
        expect(module.isParent()).toBe(true);
        const delta = { x: drag ? 80 : 0, y: drag ? 120 : 0 };
        // The person's drag translates the compound and records the same delta in the controller.
        module.position({ x: module.position("x") + delta.x, y: module.position("y") + delta.y });
        folding.move("module", delta.x, delta.y);
        dragRoutes(cy, "module", delta.x, delta.y, themes.dark);
        toggle();
        expect(module.isChildless()).toBe(true);
        expect(module.position()).toEqual({ x: original.x + delta.x, y: original.y + delta.y });
        // Separate columns leave no rows to animate: routing must still follow final placement.
        expect(frame).not.toHaveBeenCalled();
        expect({ zoom: cy.zoom(), pan: cy.pan() }).toEqual(camera);
        const calls = cy.edges(".calls");
        expect(calls).toHaveLength(2);
        expect(
          calls
            .toArray()
            .every((edge) => edge.source().id() === "module" || edge.target().id() === "module"),
        ).toBe(true);
        const routes = () => calls.map((edge) => structuredClone(edge.data("route") as Route));
        const firstCollapse = routes();
        expect(firstCollapse.every(Boolean)).toBe(true);
        // The second collapse already used to work because its remembered leaf includes the drag.
        toggle();
        toggle();
        const secondCollapse = routes();
        applyRoutes(cy, themes.dark);
        expect(secondCollapse).toEqual(routes());
        expect(firstCollapse).toEqual(routes());
      } finally {
        cy.destroy();
      }
    },
  );
}
