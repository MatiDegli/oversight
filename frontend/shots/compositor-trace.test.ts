import assert from "node:assert/strict";
import { test } from "node:test";

import { compositorTime } from "./compositor-trace.ts";
import type { TraceEvent } from "./compositor-trace.ts";

void test("compositor time unions top-level slices per thread and converts microseconds to ms", () => {
  const thread = (tid: number, name: string): TraceEvent => ({
    ph: "M",
    name: "thread_name",
    pid: 1,
    tid,
    args: { name },
  });
  const task = (
    tid: number,
    ts: number,
    dur: number,
    name = "ThreadControllerImpl::RunTask",
  ): TraceEvent => ({
    ph: "X",
    name,
    pid: 1,
    tid,
    ts,
    dur,
  });
  const result = compositorTime([
    thread(1, "Compositor"),
    thread(2, "VizCompositorThread"),
    thread(3, "CrRendererMain"),
    task(1, 1000, 2000),
    task(1, 1500, 500),
    task(1, 2500, 1000),
    task(1, 1000, 9000, "nested work"),
    task(1, 5000, 0),
    task(2, 1000, 1000),
    task(3, 1000, 9000),
  ]);
  assert.equal(result.totalMs, 3.5);
  assert.deepEqual(
    result.threads.map((item) => item.ms),
    [2.5, 1],
  );
  assert.throws(() => compositorTime([]), /no compositor tasks/);
});
