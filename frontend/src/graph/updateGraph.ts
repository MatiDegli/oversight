import type { Core, Position } from "cytoscape";

import type { GraphElement } from "./elements";

/** Update by id, retaining dragged positions and the viewport after the first layout. */
export function updateGraph(cy: Core, elements: GraphElement[]): void {
  const initial = cy.nodes().empty();
  const byId = new Map(elements.map((element) => [element.data.id, element]));
  const occupied = cy.nodes().map((node) => ({ ...node.position() }));
  let next = 0;
  cy.batch(() => {
    // Detach survivors before removing a parent (which would remove its children too).
    for (const node of cy.nodes()) {
      const definition = byId.get(node.id());
      if (definition && node.parent().first().id() !== definition.data.parent) {
        node.move({ parent: null });
      }
    }
    cy.elements()
      .filter((element) => !byId.has(element.id()))
      .remove();
    const added = elements.filter((element) => cy.getElementById(element.data.id).empty());
    cy.add(
      added.map((element) => {
        if (element.group === "edges" || element.position) return element;
        let position: Position;
        do {
          position = { x: (next % 6) * 210, y: Math.floor(next / 6) * 130 };
          next++;
        } while (
          occupied.some(
            (point) => Math.abs(point.x - position.x) < 180 && Math.abs(point.y - position.y) < 100,
          )
        );
        occupied.push(position);
        return { ...element, position };
      }),
    );
    for (const definition of elements) {
      const element = cy.getElementById(definition.data.id);
      if (definition.group === "nodes") {
        if (element.parent().first().id() !== definition.data.parent) {
          element.move({ parent: definition.data.parent ?? null });
        }
      } else if (
        element.source().id() !== definition.data.source ||
        element.target().id() !== definition.data.target
      ) {
        element.move({ source: definition.data.source, target: definition.data.target });
      }
      element.data(definition.data);
      element.classes(definition.classes);
    }
  });
  if (initial && cy.nodes().nonempty()) {
    const preset = elements.some((element) => element.position !== undefined);
    const fallback = {
      name: "fcose",
      animate: false,
      fit: true,
      padding: 40,
    };
    cy.layout(preset ? { name: "preset", fit: true, padding: 40 } : fallback).run();
  }
}
