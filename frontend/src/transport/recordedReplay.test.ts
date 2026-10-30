// @vitest-environment node
import { execFileSync, type ExecFileSyncOptionsWithStringEncoding } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadRecordedReplay } from "../../tools/recorded-replay.ts";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const recording = join(root, "fixtures/events/recorded-demo.jsonl");
const advice = "re-export a real run with make record";
const scratch: string[] = [];

function tempDir(): string {
  const path = mkdtempSync(join(tmpdir(), "oversight-replay-test-"));
  scratch.push(path);
  return path;
}

afterEach(() => {
  for (const path of scratch.splice(0)) rmSync(path, { recursive: true, force: true });
});

describe("recorded replay build gate", () => {
  // These tests start uv and the Python validator process.
  it("refuses a corrupt copy with the validator's RecordError reason", () => {
    const path = join(tempDir(), "corrupt.jsonl");
    copyFileSync(recording, path);
    copyFileSync(recording + ".provenance.json", path + ".provenance.json");
    writeFileSync(path, "not valid JSON\n");

    expect(() => loadRecordedReplay(path)).toThrow(
      `Recorded replay refused: recorded replay refused: invalid event at line 1; ${advice}`,
    );
  }, 20_000);

  it("reports ENOENT for an injected missing interpreter without changing PATH", () => {
    const missing = join(tempDir(), "missing-uv");
    const runner = (
      _command: string,
      args: readonly string[],
      options: ExecFileSyncOptionsWithStringEncoding,
    ) => execFileSync(missing, args, options);

    expect(() => loadRecordedReplay(recording, runner)).toThrow(
      `Recorded replay refused: ENOENT; ${advice}`,
    );
  });

  it("loads a valid recording using the same command and limits", () => {
    const runner = vi.fn(
      (command: string, args: readonly string[], options: ExecFileSyncOptionsWithStringEncoding) =>
        execFileSync(command, args, options),
    );
    const loaded = loadRecordedReplay(recording, runner);

    expect(runner).toHaveBeenCalledExactlyOnceWith(
      "uv",
      ["run", "--frozen", "python", "tools/validate_recording.py", recording],
      {
        cwd: root,
        encoding: "utf8",
        timeout: 60_000,
        maxBuffer: 64 * 1024 * 1024,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    expect(loaded.startsWith("export default ")).toBe(true);
    expect(loaded.endsWith(";")).toBe(true);
    const bundled = JSON.parse(loaded.slice("export default ".length, -1)) as { text: string };
    expect(bundled.text === readFileSync(recording, "utf8")).toBe(true);
  }, 20_000);

  it.each([
    { failure: { code: "ETIMEDOUT", signal: "SIGTERM" }, reason: "ETIMEDOUT" },
    { failure: { status: 23, stderr: "\n  " }, reason: "exit status 23" },
    { failure: { status: null, signal: "SIGTERM" }, reason: "signal SIGTERM" },
    {
      failure: { stderr: "earlier diagnostic\n\tfinal reason\t\n \n", status: 1 },
      reason: "final reason",
    },
    {
      failure: { stderr: Buffer.from("earlier diagnostic\r\nfinal reason\r\n"), status: 1 },
      reason: "final reason",
    },
    {
      failure: { stderr: `earlier diagnostic\n${"x".repeat(400)}\tend\n` },
      reason: "x".repeat(300),
    },
    { failure: { stderr: "reason\twith\u0000controls\n" }, reason: "reason with controls" },
    { failure: new Error("private child output"), reason: "unknown validator failure" },
    { failure: null, reason: "unknown validator failure" },
  ])("reports only the bounded diagnostic (%#)", ({ failure, reason }) => {
    const runner = () => {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- Exercise malformed child failures too.
      throw failure;
    };
    expect(() => loadRecordedReplay(recording, runner)).toThrow(
      `Recorded replay refused: ${reason}; ${advice}`,
    );
  });
});
