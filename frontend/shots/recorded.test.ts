import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "vite";

import { recordedReplayPlugin } from "../tools/recorded-replay.ts";

const root = fileURLToPath(new URL("../../", import.meta.url));
const fixture = readFileSync(join(root, "fixtures/events/handwritten-level1.jsonl"), "utf8");

async function bundle(log: string) {
  return build({
    configFile: false,
    logLevel: "silent",
    plugins: [recordedReplayPlugin()],
    build: {
      write: false,
      lib: { entry: log + "?recorded-replay", formats: ["es"], fileName: "recording" },
    },
  });
}

for (const kind of ["fixture", "fake", "invalid JSON", "invalid payload", "stale receipt"]) {
  await test(`the build refuses ${kind} instead of bundling it as a real recording`, async () => {
    const scratch = mkdtempSync(join(tmpdir(), "oversight-replay-build-"));
    try {
      const log = join(scratch, "recorded.jsonl");
      let text = fixture;
      if (kind === "fake") text = text.replace('"provider":"meta"', '"provider":"fake"');
      if (kind === "invalid JSON") text += "not JSON\n";
      if (kind === "invalid payload")
        text +=
          JSON.stringify({
            seq: 23,
            ts: "2026-10-27T14:03:13.000Z",
            type: "agent.step",
            payload: {},
          }) + "\n";
      writeFileSync(log, text);
      if (kind !== "fixture")
        writeFileSync(
          log + ".provenance.json",
          JSON.stringify({
            exporter: "oversight.record_run.v1",
            run_id: "run-20261027-140312",
            event_count: text.trim().split("\n").length,
            sha256: createHash("sha256")
              .update(kind === "stale receipt" ? "old text" : text)
              .digest("hex"),
          }),
        );
      await assert.rejects(bundle(log), /Recorded replay refused/);
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });
}

await test("the build bundles the exact bytes and provenance emitted by the exporter", async () => {
  const scratch = mkdtempSync(join(tmpdir(), "oversight-replay-build-"));
  try {
    const log = join(scratch, "recorded.jsonl");
    // A synthetic SQLite run for this offline build test only; never a product recording.
    execFileSync(
      "uv",
      [
        "run",
        "--frozen",
        "python",
        "-c",
        `
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, "tools")
from record_run import record_run
store, out = Path(sys.argv[1]), Path(sys.argv[2])
with sqlite3.connect(store) as db:
    db.execute("CREATE TABLE events (seq INTEGER, ts TEXT, type TEXT, envelope TEXT)")
    for line in Path("fixtures/events/handwritten-level1.jsonl").read_text().splitlines():
        event = json.loads(line)
        db.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                   (event["seq"], event["ts"], event["type"], line))
record_run(store, out)
`,
        join(scratch, "run.sqlite"),
        log,
      ],
      { cwd: root, timeout: 60_000 },
    );
    const result = await bundle(log);
    const outputs = Array.isArray(result) ? result : [result];
    assert.equal(outputs.length, 1);
    const output = outputs[0];
    assert.ok(output && "output" in output);
    const chunk = output.output.find((item) => item.type === "chunk");
    assert.ok(chunk);
    assert.ok(chunk.code.includes("oversight.record_run.v1"));
    assert.ok(chunk.code.includes("run-20261027-140312"));
    const generated = (await import(
      `data:text/javascript;base64,${Buffer.from(chunk.code).toString("base64")}`
    )) as { default: unknown };
    assert.deepEqual(generated.default, {
      text: readFileSync(log, "utf8"),
      provenance: JSON.parse(readFileSync(log + ".provenance.json", "utf8")) as unknown,
    });
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
});
