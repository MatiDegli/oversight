import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import type { Plugin } from "vite";

/** Ship plain notice files alongside the static site's index, with no runtime request. */
export function noticesPlugin(): Plugin {
  return {
    name: "oversight-third-party-notices",
    apply: "build",
    generateBundle() {
      const notices = fileURLToPath(new URL("../../THIRD_PARTY_NOTICES.md", import.meta.url));
      const monaco = fileURLToPath(
        new URL("../node_modules/monaco-editor/ThirdPartyNotices.txt", import.meta.url),
      );
      for (const [fileName, path] of [
        ["THIRD_PARTY_NOTICES.md", notices],
        ["monaco-editor.ThirdPartyNotices.txt", monaco],
      ] as const) {
        this.emitFile({ type: "asset", fileName, source: readFileSync(path) });
      }
    },
  };
}
