// A frame's padding is not the same on every side: the sketch gives a module a header band above
// its first row and a lane for incoming edges at its left, and gives a class a header band too.
// Cytoscape pads a compound node evenly (its per-side padding properties are aliases of one), so
// the stylesheet pads each kind of frame by its smallest side, and this file asks for the rest as a
// minimum size with all the extra on the top or the left (`min-width` and `min-height` with their
// biases): the content's own bounds plus the extra, which Cytoscape then places on that side.
//
// The extra is a function of the children's bounds, so it is recomputed, deepest frame first,
// whenever the graph is placed or a node is moved. It moves no node.
import type { BoundingBoxOptions, Core } from "cytoscape";

import { nodeGeometry } from "./geometry";
import type { GeometryTokens } from "./geometry";

/** The sides a frame of each kind pads more than the even padding the stylesheet gives it. */
export function frameExtras(theme: GeometryTokens) {
  const { outer } = nodeGeometry(theme);
  return {
    module: {
      left: outer.module.left - outer.module.right,
      top: outer.module.top - outer.module.bottom,
    },
    class: {
      left: outer.class.left - outer.class.right,
      top: outer.class.top - outer.class.bottom,
    },
  };
}

export function applyFrames(cy: Core, theme: GeometryTokens): void {
  const extras = frameExtras(theme);
  const parents = cy.nodes(":parent").toArray();
  // The deepest first: a class's size is part of its module's content.
  const depth = (id: string) => cy.getElementById(id).ancestors().length;
  parents.sort((a, b) => depth(b.id()) - depth(a.id()));
  cy.batch(() => {
    for (const frame of parents) {
      const extra = extras[frame.hasClass("class") ? "class" : "module"];
      // The children's union as Cytoscape measures it for a compound, without labels.
      // Cytoscape accepts useCache; its type definitions leave it out.
      const options: BoundingBoxOptions & { useCache: boolean } = {
        includeLabels: false,
        includeOverlays: false,
        useCache: false,
      };
      const content = frame.children().boundingBox(options);
      frame.data({ minW: content.w + extra.left, minH: content.h + extra.top });
    }
  });
}
