import type { Slot, TreeResponse } from "@contracts";
import type { State } from "../state";
import { recordedRevision } from "./recordedFiles";

type FileTree = Pick<TreeResponse, "directories" | "truncated"> & {
  files: (TreeResponse["files"][number] & { deleted?: boolean })[];
};

export interface TreeRow {
  path: string;
  name: string;
  directory: boolean;
  deleted: boolean;
  depth: number;
  parent: string;
  position: number;
  siblings: number;
}

/** Only paths carried by this run; no fabricated template files or network requests. */
export function recordedTree(state: State): FileTree {
  const paths = new Set(
    state.graph?.nodes.filter((node) => node.kind === "module").map((node) => node.path),
  );
  for (const change of Object.values(state.changes))
    for (const path of Object.keys(change.files)) paths.add(path);
  const directories = new Set<string>();
  for (const path of paths) {
    const parts = path.split("/");
    for (let end = 1; end < parts.length; end++) directories.add(parts.slice(0, end).join("/"));
  }
  return {
    directories: [...directories].sort(),
    files: [...paths].sort().map((path) => ({
      path,
      hash: null,
      deleted: recordedRevision(state, path).deleted,
    })),
    truncated: false,
  };
}

export function visibleTree(
  tree: Pick<FileTree, "directories" | "files">,
  expanded: ReadonlySet<string>,
): TreeRow[] {
  const children = new Map<string, Omit<TreeRow, "position" | "siblings">[]>();
  const add = (path: string, directory: boolean, deleted = false) => {
    const parts = path.split("/");
    const parent = parts.slice(0, -1).join("/");
    const siblings = children.get(parent) ?? [];
    siblings.push({
      path,
      name: parts.at(-1) ?? path,
      directory,
      deleted,
      depth: parts.length,
      parent,
    });
    children.set(parent, siblings);
  };
  // A bounded listing can omit a parent; synthesize its row so every listed file stays reachable.
  const directories = new Set(tree.directories);
  for (const path of [...tree.directories, ...tree.files.map((file) => file.path)]) {
    const parts = path.split("/");
    for (let end = 1; end < parts.length; end++) directories.add(parts.slice(0, end).join("/"));
  }
  for (const path of directories) add(path, true);
  for (const file of tree.files)
    if (!directories.has(file.path)) add(file.path, false, file.deleted);
  const result: TreeRow[] = [];
  const visit = (parent: string) => {
    const rows = (children.get(parent) ?? []).sort(
      (a, b) => Number(b.directory) - Number(a.directory) || (a.path < b.path ? -1 : 1),
    );
    rows.forEach((row, index) => {
      result.push({ ...row, position: index + 1, siblings: rows.length });
      if (row.directory && expanded.has(row.path)) visit(row.path);
    });
  };
  visit("");
  return result;
}

export interface FileMark {
  slot: Slot;
  mode: "read" | "edit" | "pending";
}

/** Agent letters accompany every file color; resolved changes leave no pending marker. */
export function fileMarks(state: State): Map<string, FileMark[]> {
  const result = new Map<string, FileMark[]>();
  const set = (path: string, mark: FileMark) => {
    const marks = result.get(path) ?? [];
    const old = marks.findIndex((entry) => entry.slot === mark.slot);
    if (old >= 0) marks[old] = mark;
    else marks.push(mark);
    result.set(
      path,
      marks.sort((a, b) => a.slot.localeCompare(b.slot)),
    );
  };
  for (const change of Object.values(state.changes)) {
    if (change.status === "approved" || change.status === "rejected") continue;
    const slot = state.agents[change.agent_id]?.slot;
    if (slot) for (const path of Object.keys(change.files)) set(path, { slot, mode: "pending" });
  }
  const nodes = new Map(
    [
      ...(state.graph?.nodes ?? []),
      ...Object.values(state.changes).flatMap((change) => change.graph.nodes_added),
    ].map((node) => [node.id, node.path]),
  );
  for (const focus of Object.values(state.focus)) {
    const agent = state.agents[focus.agent_id];
    if (!agent || (agent.state !== "working" && agent.state !== "revising")) continue;
    for (const id of focus.node_ids) {
      const path = nodes.get(id);
      if (path) set(path, { slot: agent.slot, mode: focus.mode });
    }
  }
  return result;
}
