import assert from "node:assert/strict";
import { test } from "node:test";

import { lastSeq, planJobs } from "./plan.ts";
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

void test("the real list: watch and review in dark run, every other entry is listed and skipped", () => {
  const jobs = planJobs(scenes, themes);
  const running = jobs.filter((job) => job.skip === null).map((job) => job.file);
  assert.deepEqual(running, ["watch-dark-bezier.png", "watch-dark-taxi.png", "review-dark.png"]);
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
