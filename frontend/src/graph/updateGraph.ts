import type { Core, Position } from "cytoscape";

import { getActiveTheme } from "../theme";

import { packFallback } from "./fallbackLayout";
import { startCamera } from "./camera";
import type { GraphElement } from "./elements";
import { FIT_PADDING, nodeGeometry } from "./geometry";
import { applyFrames } from "./frames";
import { growthPositions, placeAdditions } from "./place";
import { applyRoutes } from "./routes";
import type { RouteTheme } from "./routes";
import { writeData } from "./writes";

type GeometryTheme = Parameters<typeof nodeGeometry>[0] & RouteTheme;

// A compound's centre follows its children. Keep the last position it had as a leaf separately,
// including human drags, so removing its last child does not leave it at the frame's centre.
const leafPositions = new WeakMap<Core, Map<string, Position>>();
/**
 * The data keys a node carries only some of the time. `data()` merges, so a key the new definition
 * leaves out would keep its old value: a mark long after the focus is gone.
 */
const OPTIONAL_DATA = [
  "marks",
  "distance",
  "title",
  "count",
  "callCount",
  "exactCount",
  "possibleCount",
  "restPossible",
  "w",
  "h",
] as const;

/** Update by id, retaining dragged positions and the viewport after the first layout. */
export function updateGraph(
  cy: Core,
  elements: GraphElement[],
  theme: GeometryTheme = getActiveTheme(),
): void {
  const initial = cy.nodes().empty();
  const byId = new Map(elements.map((element) => [element.data.id, element]));
  const remembered = leafPositions.get(cy) ?? new Map<string, Position>();
  leafPositions.set(cy, remembered);
  for (const id of remembered.keys()) if (!byId.has(id)) remembered.delete(id);
  for (const node of cy.nodes(":childless"))
    if (byId.has(node.id())) remembered.set(node.id(), { ...node.position() });
  const parents = new Set(elements.map((element) => element.data.parent));
  const restore = cy
    .nodes(":parent")
    .filter((node) => byId.has(node.id()) && !parents.has(node.id()) && remembered.has(node.id()));
  const removeObsolete = () => {
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
    for (const node of restore) {
      const position = remembered.get(node.id());
      if (position) node.position(position);
    }
    const history = growthPositions.get(cy);
    for (const [root, saved] of history ?? []) {
      if ([...saved.additions].some((id) => byId.has(id))) continue;
      for (const [id, point] of saved.positions) {
        cy.getElementById(id).position(point);
        if (byId.has(id)) remembered.set(id, { ...point });
      }
      history?.delete(root);
    }
  };
  const restoringGrowth = [...(growthPositions.get(cy)?.values() ?? [])].some(
    (saved) => ![...saved.additions].some((id) => byId.has(id)),
  );
  // A restored frame must be measured at its original position before placing new arrivals.
  if (restoringGrowth) cy.batch(removeObsolete);
  const { additions, relocated } = placeAdditions(cy, elements, theme);
  cy.batch(() => {
    if (!restoringGrowth) removeObsolete();
    for (const [id, point] of relocated) cy.getElementById(id).position(point);
    cy.add(additions);
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
        element.removeClass("count-hover node-hover");
        element.move({ source: definition.data.source, target: definition.data.target });
      }
      writeData(element, definition.data, OPTIONAL_DATA);
      // Keep selection and routing state; hover survives while its geometry stays put.
      const owned = ["touching", "node-hover", "count-hover", "edge-shield", "unroutable"].filter(
        (name) => element.hasClass(name),
      );
      element.classes([...definition.classes.split(/\s+/).filter(Boolean), ...owned]);
    }
  });
  if (initial) initialLayout(cy, elements, theme);
  else settle(cy, theme);
}

/** Choose the initial layout, then settle header bands and routes before choosing the camera. */
function initialLayout(cy: Core, elements: GraphElement[], theme: GeometryTheme): void {
  const preset = elements.some((element) => element.position !== undefined);
  if (cy.nodes().nonempty()) {
    // The preset shows the graph at 100% on its hub; nothing is fitted. Without one, the layout
    // places the nodes and the camera fits them, as before.
    if (preset) cy.layout({ name: "preset", fit: false }).run();
    else {
      const fallback = { name: "fcose", animate: false, fit: true, padding: FIT_PADDING };
      cy.layout(fallback).run();
      packFallback(cy, theme);
    }
  }
  settle(cy, theme);
  // Header bands change frame bounds: choose the start camera only after they are final.
  if (preset) startCamera(cy);
}

/** After nodes are placed or moved: the frames' header bands and the edges' routes, in that order. */
export function settle(cy: Core, theme: GeometryTheme = getActiveTheme()): void {
  applyFrames(cy, theme);
  applyRoutes(cy, theme);
}
