import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";

import { checkPreset } from "../tools/check-preset.ts";

const root = resolve(import.meta.dirname, "../..");
const layouts = join(root, "fixtures/layouts");

void test("every saved preset has a README entry and contains only ids and finite positions", async () => {
  const readme = await readFile(join(layouts, "README.md"), "utf8");
  for (const file of (await readdir(layouts)).filter((name) => name.endsWith(".json"))) {
    assert(readme.includes(file), `Undocumented preset: ${file}`);
    const positions = JSON.parse(await readFile(join(layouts, file), "utf8")) as Record<
      string,
      { x: number; y: number }
    >;
    assert(Object.keys(positions).length > 0);
    for (const [id, point] of Object.entries(positions)) {
      assert(id.length > 0);
      assert.deepEqual(Object.keys(point).sort(), ["x", "y"]);
      assert(Number.isFinite(point.x) && Number.isFinite(point.y));
    }
  }
});

void test("the offline check regenerates in separate processes and rejects preset drift", async () => {
  const input = join(root, "fixtures/graph/demo-repo.json");
  const preset = join(layouts, "demo-repo.json");
  assert((await checkPreset(input, preset)) > 0);
  const scratch = await mkdtemp(join(tmpdir(), "oversight-preset-test-"));
  try {
    const changed = join(scratch, "changed.json");
    const text = await readFile(preset, "utf8");
    const drifted = text.replace(/"x": -?\d+(?:\.\d+)?/, (match) =>
      Number(match.slice(5)) === 0 ? '"x": 1' : '"x": 0',
    );
    assert.notEqual(drifted, text, "Drift injection changed nothing");
    await writeFile(changed, drifted);
    await assert.rejects(checkPreset(input, changed), /differs from/);
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
});
