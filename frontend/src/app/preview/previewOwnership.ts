import type { PreviewCandidate, PreviewIdentity } from "./previewPort";
import { previewId } from "./previewTypes";

/** Private tab recovery data. Never a public snapshot or a frame destination. */
export interface PreviewOwnership {
  identity: PreviewIdentity;
  candidate: PreviewCandidate;
  preview_id: string;
  lease_token: string;
}
export interface PreviewOwnershipStore {
  load: (identity: PreviewIdentity) => PreviewOwnership | null;
  save: (record: PreviewOwnership) => void;
  clear: (record: PreviewOwnership) => void;
}
type StoragePort = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const keys = (value: unknown, expected: string[]): value is Record<string, unknown> =>
  !!value &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  Object.keys(value).sort().join() === expected.sort().join();
const text = (value: unknown): value is string => typeof value === "string" && !!value.trim();
function parse(value: unknown): PreviewOwnership | null {
  if (!keys(value, ["identity", "candidate", "preview_id", "lease_token"])) return null;
  const { identity, candidate } = value;
  if (
    !keys(identity, ["run", "source"]) ||
    identity.source !== "live" ||
    !text(identity.run) ||
    !keys(candidate, ["run_id", "agent_id", "change_id", "revision", "head_commit"]) ||
    candidate.run_id !== identity.run ||
    ![candidate.agent_id, candidate.change_id, candidate.head_commit].every(text) ||
    !Number.isSafeInteger(candidate.revision) ||
    Number(candidate.revision) <= 0 ||
    !previewId(value.preview_id) ||
    typeof value.lease_token !== "string" ||
    !/^[A-Za-z0-9_-]{43}$/.test(value.lease_token)
  )
    return null;
  return value as unknown as PreviewOwnership;
}
export function sameOwnershipCandidate(a: PreviewCandidate, b: PreviewCandidate | null) {
  return (
    b !== null &&
    a.run_id === b.run_id &&
    a.agent_id === b.agent_id &&
    a.change_id === b.change_id &&
    a.revision === b.revision &&
    a.head_commit === b.head_commit
  );
}
/** One origin's record in this tab only. Storage refusal leaves memory ownership usable. */
export function createPreviewOwnershipStore(
  origin: string,
  storage: () => StoragePort | undefined,
): PreviewOwnershipStore {
  const key = `oversight.preview.owner:${new URL(origin).origin}`;
  return {
    load(identity) {
      if (!identity.run) return null; // App has not committed its bootstrap run yet.
      try {
        const disk = storage(),
          raw = disk?.getItem(key);
        if (!raw) return null;
        let record: PreviewOwnership | null = null;
        try {
          record = parse(JSON.parse(raw));
        } catch {
          /* Invalid tab data is discarded. */
        }
        if (
          record &&
          record.identity.run === identity.run &&
          record.identity.source === identity.source
        )
          return record;
        disk?.removeItem(key);
      } catch {
        /* Disabled, full or unavailable storage must not break App. */
      }
      return null;
    },
    save(record) {
      try {
        if (parse(record)) storage()?.setItem(key, JSON.stringify(record));
      } catch {
        /* The current owner keeps its in-memory authority. */
      }
    },
    clear(record) {
      try {
        const disk = storage();
        if (disk?.getItem(key) === JSON.stringify(record)) disk.removeItem(key);
      } catch {
        /* Clearing storage never certifies drainage. */
      }
    },
  };
}
export function tabPreviewOwnership(): PreviewOwnershipStore | undefined {
  if (typeof window === "undefined") return undefined;
  return createPreviewOwnershipStore(window.location.origin, () => window.sessionStorage);
}
