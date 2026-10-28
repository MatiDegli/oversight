import vocabulary from "../../../fixtures/events/needs-you.json";

export interface NeedsYou {
  revision: number;
  head: string;
  code: string;
  reason: string;
}

// The exported golden is checked against events/needs_you.py, the vocabulary owner.
const codes = new Set<string>(vocabulary.codes);
const pattern =
  /^Needs you — revision=(0|[1-9][0-9]*); head=([^;\s]+); code=([a-z_]+); reason=([\s\S]+)$/;

export function parseNeedsYou(message: string): NeedsYou | null {
  const match = pattern.exec(message);
  if (!match || !codes.has(match[3] ?? "") || !match[4]?.trim()) return null;
  return {
    revision: Number(match[1]),
    head: match[2] ?? "",
    code: match[3] ?? "",
    reason: match[4],
  };
}

export function matchesNeedsYou(
  note: NeedsYou | null,
  target: { revision: number; head_commit: string } | null,
): boolean {
  return (
    note !== null &&
    target !== null &&
    note.revision === target.revision &&
    note.head === target.head_commit
  );
}
