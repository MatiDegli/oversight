import type { Practice } from "./manifest";

export type PracticeCase = Practice["cases"][number];
export type SourceRow = {
  kind: "context" | "added" | "removed" | "fold";
  line: number;
  text: string;
};
export const caseSlot = (practice: Practice, entry: PracticeCase) =>
  practice.cut.agents[entry.proposal.agent_id]?.slot ?? "A";
export function changedNode(practice: Practice, entry: PracticeCase) {
  return practice.cut.graph?.nodes.find(
    (node) => node.id === entry.proposal.graph.nodes_modified[0],
  );
}
export const nodeName = (practice: Practice, entry: PracticeCase) =>
  changedNode(practice, entry)?.qualname ?? entry.proposal.task.split(" ")[0] ?? "Change";
export function calledHelper(practice: Practice, entry: PracticeCase) {
  const edge = entry.proposal.graph.edges_added.find(
    (call) =>
      call.kind === "calls" &&
      call.confidence === "exact" &&
      entry.proposal.graph.nodes_modified.includes(call.src),
  );
  return practice.cut.graph?.nodes.find((node) => node.id === edge?.dst);
}

/** Line LCS keeps signs and both versions from the recorded change.updated contents. */
export function diffRows(original: string, modified: string): SourceRow[] {
  const before = original.trimEnd().split("\n");
  const after = modified.trimEnd().split("\n");
  const lengths = Array.from(
    { length: before.length + 1 },
    () => new Uint32Array(after.length + 1),
  );
  const lengthAt = (i: number, j: number) => lengths[i]?.[j] ?? 0;
  for (let i = before.length - 1; i >= 0; i--) {
    const row = lengths[i];
    if (!row) continue;
    for (let j = after.length - 1; j >= 0; j--)
      row[j] =
        before[i] === after[j]
          ? 1 + lengthAt(i + 1, j + 1)
          : Math.max(lengthAt(i + 1, j), lengthAt(i, j + 1));
  }
  const rows: SourceRow[] = [];
  let i = 0,
    j = 0;
  while (i < before.length || j < after.length) {
    if (i < before.length && j < after.length && before[i] === after[j]) {
      rows.push({ kind: "context", line: j + 1, text: after[j] ?? "" });
      i++;
      j++;
    } else if (
      j < after.length &&
      (i === before.length || lengthAt(i, j + 1) >= lengthAt(i + 1, j))
    ) {
      rows.push({ kind: "added", line: j + 1, text: after[j++] ?? "" });
    } else rows.push({ kind: "removed", line: i + 1, text: before[i++] ?? "" });
  }
  return rows
    .filter(
      (row, index) =>
        row.kind !== "context" ||
        rows.slice(Math.max(0, index - 2), index + 3).some((other) => other.kind !== "context"),
    )
    .flatMap((row, index, kept) =>
      index > 0 && row.line > (kept[index - 1]?.line ?? 0) + 1
        ? [{ kind: "fold" as const, line: 0, text: "Unchanged lines" }, row]
        : [row],
    );
}

/** Wrap at Python boundaries, retaining every character and attaching closing punctuation. */
export function wrapSource(source: string, capacity: number): { text: string; indent: number }[] {
  const indent = Math.min((source.match(/^ */)?.[0].length ?? 0) + 2, 10);
  const parts: { text: string; indent: number }[] = [];
  let remaining = source;
  do {
    const hang = parts.length ? indent : 0;
    const limit = Math.max(8, capacity - hang);
    let end = Math.min(limit, remaining.length);
    if (end < remaining.length) {
      const candidates = [...remaining.slice(0, end).matchAll(/[ ,.([_]/g)]
        .map((match) => match.index + 1)
        .filter((point) => remaining.slice(0, point).trim());
      end = candidates.at(-1) ?? end;
      if (/^[\s\])}:,.;]+$/.test(remaining.slice(end))) {
        const earlier = candidates.filter(
          (point) => point < end && /[\w"']/.test(remaining.slice(point)),
        );
        end = earlier.at(-1) ?? remaining.length;
      }
    }
    parts.push({ text: remaining.slice(0, end), indent: hang });
    remaining = remaining.slice(end);
  } while (remaining.length);
  return parts;
}
