import type { GraphNode } from "@contracts";

/** Carry the location in the existing task text, without changing StartAgent. */
export function functionTask(node: Pick<GraphNode, "path" | "qualname">): string {
  return `In ${node.path}, ${node.qualname}: `;
}
