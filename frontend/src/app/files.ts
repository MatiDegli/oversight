import type { FileContent, FileSaved, SaveFile } from "@contracts";

import type { FileAccess, FileDraft } from "../editor";

/** App owns these maps for the page lifetime, independently of any mounted file panel. */
export function createFileSession(
  commands: {
    getFile: (path: string, agent?: string) => Promise<FileContent>;
    saveFile: (body: SaveFile) => Promise<FileSaved>;
  },
  readDrafts?: () => Map<string, FileDraft>,
  readInFlightSaves?: () => Map<string, Promise<FileSaved>>,
): FileAccess {
  const localDrafts = new Map<string, FileDraft>();
  const localSaves = new Map<string, Promise<FileSaved>>();
  const saved = new Map<string, FileContent>();
  return {
    get drafts() {
      return readDrafts?.() ?? localDrafts;
    },
    get inFlightSaves() {
      return readInFlightSaves?.() ?? localSaves;
    },
    saved,
    readFile: commands.getFile,
    saveFile(body) {
      const drafts = readDrafts?.() ?? localDrafts;
      const inFlightSaves = readInFlightSaves?.() ?? localSaves;
      const pending = inFlightSaves.get(body.path);
      if (pending) return pending;
      // Register before invoking the client, including a client double that throws synchronously.
      const result = Promise.resolve().then(() => commands.saveFile(body));
      const settled = result
        .then((answer) => {
          saved.set(body.path, {
            path: body.path,
            content: body.content,
            hash: answer.hash,
            agent_id: null,
          });
          const draft = drafts.get(body.path);
          if (draft?.content === body.content) drafts.delete(body.path);
          else if (draft) drafts.set(body.path, { ...draft, baseHash: answer.hash });
          return answer;
        })
        .finally(() => {
          inFlightSaves.delete(body.path);
        });
      inFlightSaves.set(body.path, settled);
      return settled;
    },
  };
}
