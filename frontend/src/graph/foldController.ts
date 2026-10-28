import type { Core } from "cytoscape";

import type { State } from "../state";

import { prefersReducedMotion } from "./camera";
import type { GraphElement } from "./elements";
import { foldElements } from "./foldElements";
import { slideRows } from "./foldMotion";
import { initialFolds, observeEdits, reduceFolds } from "./folds";
import { updateGraph } from "./updateGraph";

/** Owns only this view's fold choices. Replay/live events still use the same unchanged reducer. */
export function foldController(cy: Core) {
  let folds = initialFolds();
  let previous = "";
  let containers: GraphElement[] = [];
  const offsets = new Map<string, { x: number; y: number }>();
  let runId: string | undefined;
  return {
    detail(level: "Modules" | "Functions" | "Details") {
      folds = {
        ...folds,
        expanded: new Set(
          containers
            .filter(
              (e) => level === "Details" || (level === "Functions" && e.data.kind === "module"),
            )
            .map((e) => e.data.id),
        ),
      };
    },
    move(id: string, dx: number, dy: number) {
      const previous = offsets.get(id) ?? { x: 0, y: 0 };
      offsets.set(id, { x: previous.x + dx, y: previous.y + dy });
    },
    toggle(id: string): boolean {
      if (!cy.getElementById(id).is(".class, .module")) return false;
      folds = reduceFolds(folds, { type: "toggle", id });
      return true;
    },
    render(state: State, full: GraphElement[]): boolean {
      cy.scratch("syncCount", Number(cy.scratch("syncCount") ?? 0) + 1);
      const run = state.run?.run_id;
      if (run !== runId) {
        folds = initialFolds();
        runId = run;
        previous = "";
      }
      folds = observeEdits(folds, state);
      const signature = JSON.stringify([...folds.expanded].sort());
      const changed = signature !== previous;
      previous = signature;
      const initial = cy.nodes().empty();
      const previousFrames = new Set(cy.nodes(":parent").map((node) => node.id()));
      containers = full.filter((e) => e.data.kind === "class" || e.data.kind === "module");
      const expanded = folds.expanded;
      const foldedModules = new Set(
        full
          .filter((e) => e.data.kind === "module" && !expanded.has(e.data.id))
          .map((e) => e.data.id),
      );
      const projected = foldElements(full, expanded, foldedModules);
      const byId = new Map(full.map((e) => [e.data.id, e]));
      for (const element of projected.elements) {
        let root = element;
        while (root.data.parent) {
          const parent = byId.get(root.data.parent);
          if (!parent) break;
          root = parent;
        }
        const offset = offsets.get(root.data.id);
        if (offset && element.position)
          element.position = { x: element.position.x + offset.x, y: element.position.y + offset.y };
      }
      cy.scratch("foldAliases", projected.visible);
      const incoming = new Map<string, number>();
      for (const e of full)
        if (e.group === "edges" && e.data.kind === "calls" && e.data.target)
          incoming.set(e.data.target, (incoming.get(e.data.target) ?? 0) + 1);
      const hub = [...incoming].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0];
      cy.scratch("fullHub", hub ? (projected.visible.get(hub) ?? hub) : undefined);
      if (changed) {
        cy.nodes().stop();
        cy.scratch("foldAnimating", false);
      }
      updateGraph(cy, projected.elements);
      cy.nodes().ungrabify();
      cy.nodes(".module").grabify();
      if (changed && !initial) {
        const targets = new Map(
          projected.elements.flatMap((e) =>
            e.group === "nodes" && e.position ? [[e.data.id, e.position] as const] : [],
          ),
        );
        // A class becoming a row gets its own original top; rows below it are what slide.
        for (const node of cy.nodes(".class:childless, .module:childless")) {
          const position = targets.get(node.id());
          if (position && previousFrames.has(node.id())) node.position(position);
        }
        slideRows(cy, targets, prefersReducedMotion());
      }
      return changed;
    },
  };
}
