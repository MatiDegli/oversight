import type { Core } from "cytoscape";

import { visibleId } from "./foldVisibility";

/** A module includes its rows; a row also reveals its module's folded cross-file pairs. */
function callsAt(cy: Core, nodeId: string | null) {
  const node = nodeId === null ? cy.collection() : cy.getElementById(visibleId(cy, nodeId));
  return node
    .union(node.descendants())
    .connectedEdges()
    .union(node.ancestors(".module").connectedEdges(".aggregate"));
}

export function showNodeHover(cy: Core, nodeId: string | null) {
  cy.batch(() => {
    const edges = callsAt(cy, nodeId);
    cy.edges(".node-hover").difference(edges).removeClass("node-hover");
    edges.not(".node-hover").addClass("node-hover");
  });
}

/**
 * Shows the app's selection: the one node the person clicked, or none. Every element stays
 * unselectable, so Cytoscape's own taps (a background tap unselects everything) cannot change the
 * ring behind the app's back; this function lifts the lock only for its own change. The edges at
 * the selected node get the `touching` class, which brings them back from the quiet of watch mode.
 */
export function showSelection(cy: Core, nodeId: string | null) {
  cy.batch(() => {
    const elements = cy.elements();
    const target = nodeId === null ? cy.collection() : cy.getElementById(visibleId(cy, nodeId));
    elements.filter(":selectable").unselectify();
    if (!elements.filter(":selected").same(target)) {
      elements.difference(target).filter(":selected").selectify().unselect().unselectify();
      target.selectify().select().unselectify();
    }
    // The edges at the selected node are the ones worth reading, so they leave the quiet.
    const touching = callsAt(cy, nodeId);
    cy.edges(".touching").difference(touching).removeClass("touching");
    touching.not(".touching").addClass("touching");
  });
}
