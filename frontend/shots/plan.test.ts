import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { editingLog, lastSeq, logForScene, planJobs, replayEventsAfter } from "./plan.ts";
import { scenes, themes } from "./scenes.ts";

void test("a job is named <scene>-<theme>.png, scene by scene", () => {
  const jobs = planJobs(
    [
      { name: "watch", enabled: true },
      { name: "review", enabled: true },
    ],
    [
      { name: "dark", enabled: true },
      { name: "light", enabled: true },
    ],
  );
  assert.deepEqual(
    jobs.map((job) => job.file),
    ["watch-dark.png", "watch-light.png", "review-dark.png", "review-light.png"],
  );
  assert.ok(jobs.every((job) => job.skip === null));
});

void test("a disabled scene or theme is skipped with the reason, never dropped", () => {
  const jobs = planJobs(
    [
      { name: "watch", enabled: true },
      { name: "review", enabled: false, needs: "the review panel" },
    ],
    [
      { name: "dark", enabled: true },
      { name: "light", enabled: false, needs: "day mode" },
    ],
  );
  assert.equal(jobs.length, 4);
  assert.equal(jobs[0]?.skip, null);
  assert.match(jobs[1]?.skip ?? "", /theme light: day mode/);
  assert.match(jobs[2]?.skip ?? "", /scene review: the review panel/);
  assert.match(jobs[3]?.skip ?? "", /scene review: the review panel/);
});

void test("the real list: watch, review and live diff in dark run, every other entry is listed and skipped", () => {
  const jobs = planJobs(scenes, themes);
  const running = jobs.filter((job) => job.skip === null).map((job) => job.file);
  assert.deepEqual(running, [
    "watch-dark-bezier.png",
    "watch-dark-taxi.png",
    "review-dark.png",
    "live-diff-dark.png",
  ]);
  assert.deepEqual(
    scenes.map((scene) => scene.name),
    ["watch", "review", "live-diff", "file-view"],
  );
  assert.ok(scenes.every((scene) => scene.enabled || (scene.needs ?? "") !== ""));
  assert.ok(themes.every((theme) => theme.enabled || (theme.needs ?? "") !== ""));
});

void test("lastSeq reads the last event, ignoring trailing blank lines", () => {
  const log = '{"seq":1,"type":"a"}\n{"seq":2,"type":"b"}\n{"seq":7,"type":"c"}\n\n';
  assert.equal(lastSeq(log), 7);
});

void test("lastSeq rejects an empty log and a last line without a seq", () => {
  assert.throws(() => lastSeq("\n"), /empty/);
  assert.throws(() => lastSeq('{"seq":1}\n{"type":"x"}\n'), /no valid seq/);
  assert.throws(() => lastSeq('{"seq":0}\n'), /no valid seq/);
});

void test("live diff uses a derived cut through the last edit before the proposal at seq 19", () => {
  const source = readFileSync(
    new URL("../../fixtures/events/handwritten-level1-proposed.jsonl", import.meta.url),
    "utf8",
  );
  const cut = editingLog(source);
  assert.notEqual(cut, null);
  if (cut === null) throw new Error("Missing editing cut");
  const events = cut
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as { seq: number; type: string });
  assert.equal(lastSeq(cut), 12);
  assert.equal(events.at(-1)?.type, "change.updated");
  assert.ok(events.every((event, index) => event.seq === index + 1 && event.seq < 19));
  assert.ok(!cut.includes('"type":"change.proposed"'));
  assert.ok(source.startsWith(cut));
  assert.equal(scenes.find((scene) => scene.name === "live-diff")?.log, "editing");
  assert.ok(replayEventsAfter(cut, 11).startsWith('id: 12\ndata: {"seq":12,'));
  assert.equal(replayEventsAfter(cut, 12), ": replay complete\n\n");
  assert.equal(editingLog('{"seq":1,"type":"change.proposed"}'), null);
});

void test("a log without a pre-proposal edit still captures watch and review, skipping only live diff", () => {
  const source = '{"seq":1,"type":"change.proposed"}\n{"seq":2,"type":"change.updated"}\n';
  const enabled = scenes.filter((scene) => scene.enabled);
  assert.deepEqual(
    enabled.map((scene) => [scene.name, logForScene(source, scene)]),
    [
      ["watch", source],
      ["review", source],
      ["live-diff", null],
    ],
  );
  // Scenes using the served log do not even parse an editing cut.
  assert.equal(logForScene("not parsed", {}), "not parsed");
});
