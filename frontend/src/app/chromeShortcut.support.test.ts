import { spawn } from "node:child_process";
import { once } from "node:events";
import { expect, it } from "vitest";
import { stopXvfb } from "./chromeShortcut.support";

it("does not wait for a child that already exited", async () => {
  const child = spawn(process.execPath, ["-e", ""]);
  await once(child, "exit");
  await stopXvfb(child);
  expect(child.exitCode).toBe(0);
}, 2_000);

it("escalates to SIGKILL after five seconds when SIGTERM is ignored", async () => {
  const child = spawn(process.execPath, [
    "-e",
    "process.on('SIGTERM', () => {}); process.stdout.write('ready'); setInterval(() => {}, 1000);",
  ]);
  try {
    await once(child.stdout, "data");
    const exited = once(child, "exit");
    const started = performance.now();
    await stopXvfb(child);
    expect(performance.now() - started).toBeGreaterThanOrEqual(4_900);
    expect(performance.now() - started).toBeLessThan(6_000);
    expect(await exited).toEqual([null, "SIGKILL"]);
    await stopXvfb(child);
  } finally {
    child.kill("SIGKILL");
  }
}, 10_000);
