import type { Core } from "cytoscape";

/** Navigation and selection address the visible class row for a folded method. */
export function visibleId(cy: Core, id: string): string {
  const aliases = cy.scratch("foldAliases") as ReadonlyMap<string, string> | undefined;
  return aliases?.get(id) ?? id;
}
