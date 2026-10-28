import { execFileSync, type ExecFileSyncOptionsWithStringEncoding } from "node:child_process";
import { fileURLToPath } from "node:url";

import type { Plugin } from "vite";

const root = fileURLToPath(new URL("../../", import.meta.url));

type Runner = (
  command: string,
  args: readonly string[],
  options: ExecFileSyncOptionsWithStringEncoding,
) => string;

function refusalReason(error: unknown): string {
  let reason = "unknown validator failure";
  if (typeof error === "object" && error !== null) {
    const failure = error as {
      stderr?: unknown;
      code?: unknown;
      status?: unknown;
      signal?: unknown;
    };
    const stderr =
      typeof failure.stderr === "string"
        ? failure.stderr
        : Buffer.isBuffer(failure.stderr)
          ? failure.stderr.toString("utf8")
          : "";
    const lastLine = stderr
      .split(/[\r\n\u2028\u2029]/)
      .map((line) => line.trim())
      .filter(Boolean)
      .at(-1);
    if (typeof failure.code === "string" && failure.code) reason = failure.code;
    else if (lastLine) reason = lastLine;
    else if (typeof failure.status === "number") reason = `exit status ${String(failure.status)}`;
    else if (typeof failure.signal === "string" && failure.signal)
      reason = `signal ${failure.signal}`;
  }
  return reason
    .replace(/[\p{Cc}\p{Zl}\p{Zp}]/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 300);
}

/** Python uses the source contracts, not generated TypeScript casts, to check every line. */
export function loadRecordedReplay(path: string, runner: Runner = execFileSync): string {
  try {
    const verified = runner(
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
  } catch (error) {
    // Child errors include captured stdout; attaching a cause would expose the recording.
    // eslint-disable-next-line preserve-caught-error
    throw new Error(
      `Recorded replay refused: ${refusalReason(error)}; re-export a real run with make record`,
    );
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
