import type { Core } from "cytoscape";

import { visibleId } from "./foldVisibility";

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
    elements.unselectify();
    elements.difference(target).filter(":selected").selectify().unselect().unselectify();
    target.selectify().select().unselectify();
    // The edges at the selected node are the ones worth reading, so they leave the quiet.
    cy.edges().removeClass("touching");
    target.connectedEdges().addClass("touching");
  });
}
