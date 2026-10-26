import cytoscape from "cytoscape";
import { expect, it } from "vitest";

import { showSelection } from "./selection";

const selected = (cy: cytoscape.Core) => cy.$(":selected").map((node) => node.id());

it("rings exactly the app's node, and Cytoscape's own taps cannot change it", () => {
  const cy = cytoscape({
    headless: true,
    elements: ["a", "b", "c"].map((id) => ({ data: { id }, position: { x: 0, y: 0 } })),
  });
  try {
    showSelection(cy, "a");
    expect(selected(cy)).toEqual(["a"]);
    // A background tap unselects everything; a tap on another node selects it.
    cy.elements().unselect();
    cy.getElementById("b").select();
    expect(selected(cy)).toEqual(["a"]);

    showSelection(cy, "b");
    expect(selected(cy)).toEqual(["b"]);
    cy.add({ data: { id: "d" }, position: { x: 0, y: 0 } }).select();
    showSelection(cy, "b");
    expect(selected(cy)).toEqual(["b"]);
    cy.getElementById("d").select();
    expect(selected(cy)).toEqual(["b"]);

    showSelection(cy, null);
    expect(selected(cy)).toEqual([]);
    showSelection(cy, "missing");
    expect(selected(cy)).toEqual([]);
  } finally {
    cy.destroy();
  }
});
