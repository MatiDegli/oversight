// The log the browser run plays: the hand-written level 1 log with one difference, which the
// hand-written log itself lacks: the agent's change adds a function (cap_discount), so the merge's
// graph.delta adds a node to main. A delta that only changes lines shows nothing on the graph, so
// only a log that adds a node can prove the page applies the delta. vite.config.ts in this folder
// builds the replay site with this log in place of the bundled one; replay.spec.ts reads the same file.
export const E2E_LOG = "handwritten-level1-adds-node.jsonl";
