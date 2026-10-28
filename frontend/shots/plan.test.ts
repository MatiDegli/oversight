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

void test("the real list: watch, review, live diff, task slots and file view run in dark; light remains skipped", () => {
  const jobs = planJobs(scenes, themes);
  const running = jobs.filter((job) => job.skip === null).map((job) => job.file);
  assert.deepEqual(running, [
    "watch-dark.png",
    "watch-fit-dark.png",
    "review-dark.png",
    "live-diff-dark.png",
    "focus-dark.png",
    "task-one-slot-dark.png",
    "task-two-slots-braked-dark.png",
    "file-view-dark.png",
    "file-view-task-dark.png",
  ]);
  assert.deepEqual(
    scenes.map((scene) => scene.name),
    [
      "watch",
      "watch-fit",
      "review",
      "live-diff",
      "focus",
      "task-one-slot",
      "task-two-slots-braked",
      "file-view",
      "file-view-task",
    ],
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

void test("a log without a pre-proposal edit still captures watch, review and file view, skipping only live diff", () => {
  const source = '{"seq":1,"type":"change.proposed"}\n{"seq":2,"type":"change.updated"}\n';
  const enabled = scenes.filter(
    (scene) => scene.enabled && (!scene.log || ["editing", "proposed"].includes(scene.log)),
  );
  assert.deepEqual(
    enabled.map((scene) => [scene.name, logForScene(source, scene)]),
    [
      ["watch", source],
      ["watch-fit", source],
      ["review", source],
      ["live-diff", null],
      ["focus", null],
      ["file-view", source],
      ["file-view-task", source],
    ],
  );
  // Scenes using the served log do not even parse an editing cut.
  assert.equal(logForScene("not parsed", {}), "not parsed");
});

void test("watch and review use the real recording through both proposals, before either merge", () => {
  const source = readFileSync(
    new URL("../../fixtures/events/recorded-demo.jsonl", import.meta.url),
    "utf8",
  );
  const cut = logForScene(source, { log: "proposed" });
  assert.ok(cut);
  assert.ok(source.startsWith(cut));
  const events = cut
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as { type: string });
  assert.equal(events.filter((event) => event.type === "change.proposed").length, 2);
  assert.ok(events.every((event) => event.type !== "change.resolved"));
  for (const name of ["watch", "watch-fit", "review"])
    assert.equal(scenes.find((scene) => scene.name === name)?.log, "proposed");
});

void test("task scenes replay one offered slot and two with a non-Meta tick carrying A's brake", () => {
  const source = readFileSync(
    new URL("../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
    "utf8",
  );
  const one = logForScene(source, { log: "one-slot" });
  assert.ok(one);
  assert.equal(lastSeq(one), 2);
  assert.deepEqual(
    (
      JSON.parse(one.split("\n")[0] ?? "") as { payload: { slots: { slot: string }[] } }
    ).payload.slots.map((info) => info.slot),
    ["A"],
  );
  const two = readFileSync(
    new URL("../../fixtures/events/handwritten-two-slots-braked.jsonl", import.meta.url),
    "utf8",
  );
  const events = two
    .trim()
    .split("\n")
    .map(
      (line) => JSON.parse(line) as { seq: number; type: string; payload: Record<string, unknown> },
    );
  assert.deepEqual(
    events.map((event) => event.seq),
    [1, 2, 3, 4, 5, 6],
  );
  assert.deepEqual(events[0]?.payload.slots, [
    { slot: "A", default_model: "muse-spark" },
    { slot: "B", default_model: "muse-spark-open" },
  ]);
  const tick = events.find((event) => event.type === "usage.tick");
  assert.ok(tick);
  assert.equal(tick.payload.brake, "blocked");
  assert.equal(tick.payload.cost_usd, null);
  // The shared Meta ledger was already spent before this Baseten agent started.
  assert.equal(
    (events[0].payload.budget as { spent_usd: number }).spent_usd,
    tick.payload.spent_usd,
  );
  // B's change is resolved, so slot B is free and the preselection can reach it.
  assert.equal(events.at(-1)?.type, "change.resolved");
  assert.equal(events.at(-1)?.payload.change_id, "change-2");
  assert.equal(
    scenes.find((scene) => scene.name === "task-two-slots-braked")?.log,
    "handwritten-two-slots-braked.jsonl",
  );
});
