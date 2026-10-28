import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const evidence = JSON.parse(
  readFileSync(new URL("./compositor-evidence.json", import.meta.url), "utf8"),
) as {
  before: Measurement;
  after: Measurement;
};
interface Measurement {
  commit: string;
  samplesMs: number[];
  medianMs: number;
  frames: number;
  fieldDraws: number;
  motionSha256: string;
}

void test("M8 publishes the measured compositor medians, delta and sample counts", () => {
  const decisions = readFileSync(new URL("../../DECISIONS.md", import.meta.url), "utf8");
  const m8 = decisions.split("M8: bounded backdrop blur")[1] ?? "";
  for (const label of ["before", "after"] as const) {
    const measurement = evidence[label];
    const { samplesMs, medianMs, commit } = measurement;
    assert.equal(samplesMs.length, 3);
    assert.equal([...samplesMs].sort((a, b) => a - b)[1], medianMs);
    assert.ok(
      m8.includes(
        `| ${label} | \`${commit.slice(0, 7)}\` | ${String(samplesMs.length)} | ${medianMs.toFixed(3)} |`,
      ),
    );
  }
  const delta = evidence.after.medianMs - evidence.before.medianMs;
  const percent = (delta / evidence.before.medianMs) * 100;
  assert.ok(
    m8.includes(`Delta (after − before): ${delta.toFixed(3)} ms (${percent.toFixed(3)}%).`),
  );
  assert.ok(m8.includes("SwiftShader"));
  assert.ok(m8.includes("compositor-evidence.json"));
  assert.equal(evidence.before.motionSha256, evidence.after.motionSha256);
  assert.equal(evidence.before.frames, 180);
  assert.equal(evidence.before.fieldDraws, 177);
  assert.equal(evidence.after.frames, evidence.before.frames);
  assert.equal(evidence.after.fieldDraws, evidence.before.fieldDraws);
});
