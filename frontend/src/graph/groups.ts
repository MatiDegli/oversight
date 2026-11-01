import type { GraphNode } from "@contracts";

export interface FolderGroup {
  id: string;
  label: string;
  moduleIds: string[];
  panel: boolean;
}
export type NamedGroups = ReadonlyMap<
  string,
  readonly { label: string; moduleIds: readonly string[] }[]
>;

/** The first folder level that separates drawn modules; tests always keep their own group. */
export function folderGroups(nodes: readonly GraphNode[], named?: NamedGroups): FolderGroup[] {
  const modules = nodes.filter((node) => node.kind === "module");
  const paths = modules
    .filter((node) => !node.is_test)
    .map((node) => node.path.split("/").slice(0, -1));
  let depth = 1;
  while (
    !modules.some((node) => node.is_test) &&
    paths.length &&
    paths.every((p) => p.length >= depth) &&
    new Set(paths.map((p) => p.slice(0, depth).join("/"))).size === 1 &&
    paths.some((p) => p.length > depth)
  )
    depth += 1;
  const buckets = new Map<string, GraphNode[]>();
  for (const node of modules) {
    const parts = node.path.split("/").slice(0, -1);
    const id = node.is_test ? "tests" : parts.slice(0, depth).join("/");
    buckets.set(id, [...(buckets.get(id) ?? []), node]);
  }
  if (buckets.size < 2) return [];
  return [...buckets]
    .sort(([a], [b]) => a.localeCompare(b))
    .flatMap(([folder, members]) => {
      // A's grouping service plugs in here. Only a flat folder over twelve modules accepts names.
      const supplied =
        folder !== "tests" &&
        members.length > 12 &&
        members.every((m) => m.path.split("/").slice(0, -1).join("/") === folder)
          ? named?.get(folder)
          : undefined;
      if (supplied)
        return supplied.map(({ label, moduleIds }) => ({
          id: `${folder}/${label}`,
          label,
          moduleIds: [...moduleIds],
          panel: moduleIds.length > 1,
        }));
      return [
        {
          id: folder,
          label: folder,
          moduleIds: members.map((m) => m.id).sort(),
          panel: folder !== "" && members.length > 1,
        },
      ];
    });
}
