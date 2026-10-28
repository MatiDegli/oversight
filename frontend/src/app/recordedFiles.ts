import type { FileAccess } from "../editor";
import type { State } from "../state";

/** Share revision selection between the recorded tree and the file view. */
export function recordedRevision(state: State, path: string, agent?: string) {
  const changes = Object.values(state.changes).filter(
    (change) => path in change.files && (agent === undefined || change.agent_id === agent),
  );
  const merged = changes
    .filter((change) => change.resolution?.merge === "merged")
    .sort((a, b) => (a.resolutionSeq ?? 0) - (b.resolutionSeq ?? 0));
  const change =
    agent === undefined
      ? (merged.at(-1) ?? changes[0])
      : changes.sort((a, b) => (b.fileUpdateSeq?.[path] ?? 0) - (a.fileUpdateSeq?.[path] ?? 0))[0];
  const file = change?.files[path]?.file;
  const modified = agent !== undefined || merged.length > 0;
  return {
    content: modified ? file?.modified : file?.original,
    deleted: modified && file?.modified === null,
  };
}

/** Recorded contents only. Snapshot paths without a recorded edit have no source bytes. */
export function createRecordedFiles(readState: () => State): FileAccess {
  return {
    readOnly: true,
    drafts: new Map(),
    inFlightSaves: new Map(),
    saved: new Map(),
    readFile: async (path, agent) => {
      const { content, deleted } = recordedRevision(readState(), path, agent);
      if (deleted)
        throw new Error(
          "This file was deleted in this change. Open its recorded diff to inspect the deletion.",
        );
      if (content === null || content === undefined)
        throw new Error(
          "This recording contains the file path but no source contents. Open a recorded diff to inspect its change.",
        );
      const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
      const hash = Array.from(new Uint8Array(bytes), (byte) =>
        byte.toString(16).padStart(2, "0"),
      ).join("");
      return { path, content, hash, agent_id: agent ?? null };
    },
    saveFile: () => Promise.reject(new Error("Recorded files are read-only.")),
  };
}
