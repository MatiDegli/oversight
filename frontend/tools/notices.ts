import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { Plugin } from "vite";

const require = createRequire(import.meta.url);

/** Ship plain notice files alongside the static site's index, with no runtime request. */
export function noticesPlugin(): Plugin {
  return {
    name: "oversight-third-party-notices",
    apply: "build",
    generateBundle() {
      const notices = fileURLToPath(new URL("../../THIRD_PARTY_NOTICES.md", import.meta.url));
      const monaco = join(
        dirname(require.resolve("monaco-editor/package.json")),
        "ThirdPartyNotices.txt",
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
