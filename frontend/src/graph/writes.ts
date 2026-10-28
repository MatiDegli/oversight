import type { CollectionData } from "cytoscape";

/** Marks and routes contain JSON values and are rebuilt on each event. */
const equal = (a: unknown, b: unknown) =>
  a === b ||
  (a !== null &&
    b !== null &&
    typeof a === "object" &&
    typeof b === "object" &&
    JSON.stringify(a) === JSON.stringify(b));

/** Equal writes still restyle. Clear omitted optional keys in the same data call. */
export function writeData(
  element: CollectionData,
  values: Record<string, unknown>,
  optional: readonly string[] = [],
): void {
  const current = element.data() as Record<string, unknown>;
  const changes: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    // Topology is changed through move(), never data().
    if (["id", "parent", "source", "target"].includes(key) || equal(current[key], value)) continue;
    changes[key] = value;
  }
  for (const key of optional)
    if (!(key in values) && current[key] !== undefined) changes[key] = undefined;
  if (Object.keys(changes).length) element.data(changes);
}
