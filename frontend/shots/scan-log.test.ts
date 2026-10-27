import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test } from "node:test";

import { settleLog } from "./scan-log.ts";

const proposed = readFileSync(
  resolve(import.meta.dirname, "../../fixtures/events/handwritten-level1-proposed.jsonl"),
  "utf8",
);

void test("every event takes the first one's time, and nothing else changes", () => {
  const before = proposed
    .split("\n")
    .filter((line) => line !== "")
    .map((line) => JSON.parse(line) as { seq: number; ts: string });
  const after = settleLog(proposed)
    .split("\n")
    .filter((line) => line !== "")
    .map((line) => JSON.parse(line) as { seq: number; ts: string });
  assert.equal(after.length, before.length);
  assert.ok(new Set(before.map((event) => event.ts)).size > 1, "the source log has gaps to remove");
  assert.deepEqual(
    new Set(after.map((event) => event.ts)),
    new Set([before[0]?.ts]),
    "one time for every event: the replay waits for none",
  );
  const withoutTime = (event: { ts: string }): object => ({ ...event, ts: "" });
  assert.deepEqual(after.map(withoutTime), before.map(withoutTime));
});

void test("an empty log, or one whose first event has no ts, is refused", () => {
  assert.throws(() => settleLog(""), /empty/);
  assert.throws(() => settleLog('{"seq":1}\n'), /no ts/);
});
