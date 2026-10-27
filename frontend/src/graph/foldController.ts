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
  let runId: string | undefined;
  return {
    toggle(id: string): boolean {
      if (!cy.getElementById(id).hasClass("class")) return false;
      folds = reduceFolds(folds, { type: "toggle", id });
      return true;
    },
    render(state: State, full: GraphElement[]): boolean {
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
      const projected = foldElements(full, folds.expanded);
      cy.scratch("foldAliases", projected.visible);
      if (changed) {
        cy.nodes().stop();
        cy.scratch("foldAnimating", false);
      }
      updateGraph(cy, projected.elements);
      if (changed && !initial) {
        const targets = new Map(
          projected.elements.flatMap((e) =>
            e.group === "nodes" && e.position ? [[e.data.id, e.position] as const] : [],
          ),
        );
        // A class becoming a row gets its own original top; rows below it are what slide.
        for (const node of cy.nodes(".class:childless")) {
          const position = targets.get(node.id());
          if (position) node.position(position);
        }
        slideRows(cy, targets, prefersReducedMotion());
      }
      return changed;
    },
  };
}
