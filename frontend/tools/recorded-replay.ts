import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import type { Plugin } from "vite";

const root = fileURLToPath(new URL("../../", import.meta.url));

/** Python uses the source contracts, not generated TypeScript casts, to check every line. */
export function loadRecordedReplay(path: string): string {
  try {
    const verified = execFileSync(
      "uv",
      ["run", "--frozen", "python", "tools/validate_recording.py", path],
      {
        cwd: root,
        encoding: "utf8",
        timeout: 60_000,
        maxBuffer: 64 * 1024 * 1024,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    return `export default ${verified.trim()};`;
  } catch {
    throw new Error("Recorded replay refused: re-export a real run with make record");
  }
}

export function recordedReplayPlugin(): Plugin {
  return {
    name: "oversight-verified-recording",
    enforce: "pre",
    load(id) {
      if (!id.endsWith("?recorded-replay")) return null;
      const path = id.slice(0, -"?recorded-replay".length);
      this.addWatchFile(path);
      this.addWatchFile(path + ".provenance.json");
      return loadRecordedReplay(path);
    },
  };
}
