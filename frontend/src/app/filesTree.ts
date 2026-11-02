import type { Slot, TreeResponse } from "@contracts";
import type { State } from "../state";
import { selectWorkPresence } from "./workPresence";
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
  agentId: string;
  slot: Slot;
  mode: "read" | "edit" | "pending";
}

/** Current focus wins over the same agent's waiting change; other identities stay visible. */
export function fileMarks(state: State): Map<string, FileMark[]> {
  const result = new Map<string, FileMark[]>();
  const { current, pending } = selectWorkPresence(state);
  for (const group of [...current, ...pending]) {
    for (const { path } of group.files) {
      const marks = result.get(path) ?? [];
      if (!marks.some((mark) => mark.agentId === group.agentId))
        marks.push({ agentId: group.agentId, slot: group.slot, mode: group.mode });
      result.set(path, marks);
    }
  }
  return result;
}
