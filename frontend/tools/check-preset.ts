// Offline, explicit check: no fetching and no snapshot/source text is committed.
// node frontend/tools/check-preset.ts <snapshot.json> <preset.json>
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

import type { GraphSnapshot } from "../../contracts/generated/contracts.ts";

export async function checkPreset(input: string, preset: string): Promise<number> {
  const snapshot = JSON.parse(await readFile(input, "utf8")) as GraphSnapshot;
  const expected = await readFile(preset);
  const points = JSON.parse(expected.toString()) as GraphSnapshot["positions"];
  assert.deepEqual(Object.keys(points), snapshot.nodes.map(({ id }) => id).sort());
  for (const point of Object.values(points)) {
    assert.deepEqual(Object.keys(point).sort(), ["x", "y"]);
    assert(Number.isFinite(point.x) && Number.isFinite(point.y));
  }
  const scratch = await mkdtemp(join(tmpdir(), "oversight-preset-"));
  try {
    for (const name of ["first.json", "second.json"]) {
      const output = join(scratch, name);
      await promisify(execFile)(
        process.execPath,
        [join(import.meta.dirname, "layout.ts"), "--input", input, "--output", output],
        { timeout: 30_000 },
      );
      assert.deepEqual(await readFile(output), expected, `${name} differs from ${preset}`);
    }
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
  return Object.keys(points).length;
}

if (import.meta.main) {
  const [input, preset] = process.argv.slice(2);
  if (!input || !preset) throw new Error("Usage: check-preset.ts <snapshot.json> <preset.json>");
  console.log(
    `preset: ${String(await checkPreset(input, preset))} ids; two byte-identical regenerations`,
  );
}
