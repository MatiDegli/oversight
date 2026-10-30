import { readFileSync } from "node:fs";

import type { GraphSnapshot } from "@contracts";

/** Optional offline export; regeneration and provenance live in fixtures/layouts/README.md. */
export function readBoltonsSnapshot(root = new URL("../../../", import.meta.url)) {
  let content: string;
  try {
    content = readFileSync(new URL(".runtime/graphs/boltons-d31d821.snapshot.json", root), "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
  const graph = JSON.parse(content) as GraphSnapshot;
  graph.positions = JSON.parse(
    readFileSync(new URL("fixtures/layouts/boltons-d31d821.json", root), "utf8"),
  ) as GraphSnapshot["positions"];
  return graph;
}
