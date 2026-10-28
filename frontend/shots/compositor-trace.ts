/** Chromium trace durations are microseconds; count top-level tasks only, never nested slices. */
export interface TraceEvent {
  ph: string;
  name: string;
  pid: number;
  tid: number;
  ts?: number;
  dur?: number;
  args?: { name?: string };
}

export function compositorTime(events: TraceEvent[]) {
  const threads = new Map<string, string>();
  for (const event of events)
    if (event.ph === "M" && event.name === "thread_name" && event.args?.name)
      threads.set(`${String(event.pid)}:${String(event.tid)}`, event.args.name);
  const intervals = new Map<string, [number, number][]>();
  for (const event of events) {
    const key = `${String(event.pid)}:${String(event.tid)}`;
    if (
      ["Compositor", "VizCompositorThread"].includes(threads.get(key) ?? "") &&
      event.ph === "X" &&
      event.name === "ThreadControllerImpl::RunTask" &&
      event.ts !== undefined &&
      event.dur !== undefined &&
      event.dur > 0
    ) {
      const slices = intervals.get(key) ?? [];
      slices.push([event.ts, event.ts + event.dur]);
      intervals.set(key, slices);
    }
  }
  const measured = [...intervals].map(([key, slices]) => {
    let end = -Infinity;
    let microseconds = 0;
    for (const [start, finish] of slices.sort((a, b) => a[0] - b[0])) {
      microseconds += Math.max(0, finish - Math.max(start, end));
      end = Math.max(end, finish);
    }
    return { thread: threads.get(key), ms: microseconds / 1000, tasks: slices.length };
  });
  if (measured.length === 0) throw new Error("Trace contains no compositor tasks");
  return { totalMs: measured.reduce((sum, thread) => sum + thread.ms, 0), threads: measured };
}
