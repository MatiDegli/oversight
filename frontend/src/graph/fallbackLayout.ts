import type { Core } from "cytoscape";

import { applyFrames } from "./frames";
import { FIT_PADDING, nodeGeometry } from "./geometry";
import type { GeometryTokens } from "./geometry";
import { packColumns } from "./packColumns";

/** fcose arranges contents; only final frame sizes decide where top-level cards can fit. */
export function packFallback(cy: Core, theme: GeometryTokens): void {
  applyFrames(cy, theme);
  const g = nodeGeometry(theme);
  const roots = cy.nodes(":orphan").sort((a, b) => a.id().localeCompare(b.id()));
  const cards = roots.map((node) => {
    const box = node.boundingBox({ includeLabels: true, includeOverlays: false });
    return { id: node.id(), width: box.w, height: box.h, box };
  });
  // Conservative bounds add a stroke on each side. Reserve it within the token's gap so the
  // drawn cards keep the same rhythm as the preset (and its readable overview height).
  const stroke = 2 * g.border;
  const positions = packColumns(
    cards,
    9 * (g.headerHeight + g.rowGap),
    Math.max(0, g.gutter - stroke),
    Math.max(0, g.rowGap - stroke),
  );
  cy.batch(() => {
    for (const card of cards) {
      const position = positions.get(card.id);
      if (!position) continue;
      const dx = position.x - (card.box.x1 + card.box.x2) / 2;
      const dy = position.y - (card.box.y1 + card.box.y2) / 2;
      const root = cy.getElementById(card.id);
      // Move leaves once: compounds follow them, preserving their internal arrangement.
      for (const leaf of root.union(root.descendants()).filter(":childless"))
        leaf.position({ x: leaf.position("x") + dx, y: leaf.position("y") + dy });
    }
  });
  applyFrames(cy, theme);
  cy.fit(cy.nodes(), FIT_PADDING);
}
