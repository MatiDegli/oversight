import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "vite";

import type { ChosenReplaySource, RecordingProvenance } from "../src/transport/replay-source.ts";
import { recordedReplayPlugin } from "../tools/recorded-replay.ts";

const root = fileURLToPath(new URL("../../", import.meta.url));
const original = join(root, "frontend/src/transport");
const recording = join(root, "fixtures/events/recorded-demo.jsonl");
const provenance = JSON.parse(
  readFileSync(recording + ".provenance.json", "utf8"),
) as RecordingProvenance;

async function publicReplay(files: "both" | "no recording" | "no receipt") {
  const scratch = mkdtempSync(join(tmpdir(), "oversight-public-replay-"));
  try {
    const transport = join(scratch, "frontend/src/transport");
    const fixtures = join(scratch, "fixtures/events");
    mkdirSync(transport, { recursive: true });
    mkdirSync(fixtures, { recursive: true });
    const entry = join(transport, "index.ts");
    // Build the actual public entry in a separate tree; never rename the shared fixture.
    copyFileSync(join(original, "index.ts"), entry);
    copyFileSync(
      join(root, "fixtures/events/handwritten-level1.jsonl"),
      join(fixtures, "handwritten-level1.jsonl"),
    );
    if (files !== "no recording") copyFileSync(recording, join(fixtures, "recorded-demo.jsonl"));
    if (files === "both")
      copyFileSync(
        recording + ".provenance.json",
        join(fixtures, "recorded-demo.jsonl.provenance.json"),
      );
    const result = await build({
      configFile: false,
      root: join(scratch, "frontend"),
      mode: "replay",
      logLevel: "silent",
      plugins: [
        {
          name: "public-entry-dependencies",
          resolveId(source, importer) {
            if (importer === entry && source.startsWith("./"))
              return join(original, source + ".ts");
            return null;
          },
        },
        recordedReplayPlugin(),
      ],
      build: { write: false, lib: { entry, formats: ["es"] } },
    });
    const outputs = Array.isArray(result) ? result : [result];
    assert.equal(outputs.length, 1);
    const output = outputs[0];
    assert.ok(output && "output" in output);
    const chunks = output.output.filter((item) => item.type === "chunk");
    assert.equal(chunks.length, 1);
    assert.ok(chunks[0]);
    const module = (await import(
      `data:text/javascript;base64,${Buffer.from(chunks[0].code).toString("base64")}`
    )) as { replaySource: ChosenReplaySource };
    return module.replaySource;
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

await test("the public replay build defaults to the exact verified two-agent recording", async () => {
  const source = await publicReplay("both");
  assert.equal(source.text, readFileSync(recording, "utf8"));
  assert.equal(createHash("sha256").update(source.text).digest("hex"), provenance.sha256);
  assert.equal(source.kind, "real recorded run");
  assert.equal(source.label, "Recorded run · Oct 27, 2026");
  const first = JSON.parse(source.text.split("\n")[0] ?? "") as {
    ts: string;
    payload: { run_id: string };
  };
  assert.equal(first.ts, "2026-10-27T18:52:14.527Z");
  assert.equal(first.payload.run_id, provenance.run_id);
});

for (const missing of ["no recording", "no receipt"] as const) {
  await test(`the public replay build refuses ${missing} instead of publishing the fixture`, async () => {
    await assert.rejects(
      publicReplay(missing),
      /Recorded replay refused|Could not resolve .*recorded-demo\.jsonl/,
    );
  });
}
