import type { State } from "../../state";

export const groups = ["Functions, methods and classes", "Files", "Commands"] as const;
export type CommandId = "sidebar" | "dock" | "close" | "maximize" | "rescan" | "task";
export interface PaletteCommand {
  id: CommandId;
  label: string;
  unavailable?: string;
  run: () => void;
}
export interface PaletteItem {
  id: string;
  group: (typeof groups)[number];
  name: string;
  detail: string;
  qualname?: string;
  path?: string;
  kind: string;
  unavailable?: string;
  run: () => void;
}

export function paletteItems(
  state: State,
  paths: readonly string[],
  onNode: (id: string) => void,
  onFile: (path: string) => void,
  commands: readonly PaletteCommand[],
): PaletteItem[] {
  return [
    ...(state.graph?.nodes ?? [])
      .filter((node) => node.kind !== "module")
      .map((node) => ({
        id: `node:${node.id}`,
        group: groups[0],
        name: node.name,
        detail: `${node.qualname} · ${node.path}`,
        qualname: node.qualname,
        path: node.path,
        kind: node.kind,
        run: () => {
          onNode(node.id);
        },
      })),
    ...paths.map((path) => ({
      id: `file:${path}`,
      group: groups[1],
      name: path.split("/").at(-1) ?? path,
      detail: path,
      path,
      kind: "file",
      run: () => {
        onFile(path);
      },
    })),
    ...commands.map((command) => ({
      ...command,
      id: `command:${command.id}`,
      group: groups[2],
      name: command.label,
      detail: command.unavailable ?? "",
      kind: "command",
    })),
  ];
}

/** Subsequence matching, with contiguous names ahead of scattered names, then paths. */
function fuzzy(query: string, value: string): number {
  const text = value.toLowerCase();
  if (text === query) return 0;
  if (text.startsWith(query)) return 10;
  if (text.includes(query)) return 20;
  let cursor = 0;
  let gaps = 0;
  for (const letter of query) {
    const index = text.indexOf(letter, cursor);
    if (index < 0) return Infinity;
    gaps += index - cursor;
    cursor = index + 1;
  }
  return 30 + gaps / (gaps + 1);
}

export function searchItems(items: readonly PaletteItem[], query: string): PaletteItem[] {
  const normalized = query.trim().toLowerCase();
  const score = (item: PaletteItem) =>
    normalized
      ? Math.min(
          fuzzy(normalized, item.name),
          item.qualname ? 1 + fuzzy(normalized, item.qualname) : Infinity,
          item.path ? (item.kind === "file" ? 99 : 100) + fuzzy(normalized, item.path) : Infinity,
        )
      : 0;
  const ranked = items
    .map((item) => ({ item, score: score(item) }))
    .filter((entry) => Number.isFinite(entry.score))
    .sort(
      (a, b) =>
        a.score - b.score ||
        groups.indexOf(a.item.group) - groups.indexOf(b.item.group) ||
        a.item.name.localeCompare(b.item.name) ||
        a.item.id.localeCompare(b.item.id),
    )
    .map(({ item }) => item);
  // Keep groups contiguous in keyboard/DOM order, with the best matching group first.
  return [...new Set(ranked.map((item) => item.group))].flatMap((group) =>
    ranked.filter((item) => item.group === group),
  );
}
