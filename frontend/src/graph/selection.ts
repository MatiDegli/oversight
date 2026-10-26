import type { Core } from "cytoscape";

/**
 * Shows the app's selection: the one node the person clicked, or none. Every element stays
 * unselectable, so Cytoscape's own taps (a background tap unselects everything) cannot change the
 * ring behind the app's back; this function lifts the lock only for its own change.
 */
export function showSelection(cy: Core, nodeId: string | null) {
  cy.batch(() => {
    const elements = cy.elements();
    const target = nodeId === null ? cy.collection() : cy.getElementById(nodeId);
    elements.unselectify();
    elements.difference(target).filter(":selected").selectify().unselect().unselectify();
    target.selectify().select().unselectify();
  });
}
