import type { Core } from "cytoscape";

/** View state is not style input: the scratch setter would restyle the entire graph. */
export function setScratch(cy: Core, key: string, value: unknown): void {
  const scratch = cy.scratch() as Record<string, unknown>;
  scratch[key] = value;
}
