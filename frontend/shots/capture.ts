// Captures 1920x1080 PNGs of the served UI into .shots/, one per enabled scene and theme, named
// `<scene>-<theme>[-<edge-shape>].png`, replacing the previous set. Started by make shots, which serves
// the replay and Vite first:
//
//   node shots/capture.ts --url http://127.0.0.1:5711/ --log ../fixtures/events/x.jsonl --out ../.shots
import { mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { once } from "node:events";
import { createServer } from "node:http";
import { join, resolve } from "node:path";
import { exit, stdout } from "node:process";
import { parseArgs } from "node:util";

import { chromium } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import type { GraphProbeHost } from "../src/graph/probe.ts";

import { editingLog, lastSeq, planJobs } from "./plan.ts";
import { streamReplay } from "./replay.ts";
import { scenes, themes } from "./scenes.ts";

const VIEWPORT = { width: 1920, height: 1080 };
const TIMEOUT_MS = 30_000;

const { values } = parseArgs({
  options: {
    url: { type: "string" },
    log: { type: "string" },
    out: { type: "string", default: resolve(import.meta.dirname, "../../.shots") },
  },
});
if (values.url === undefined || values.log === undefined) {
  throw new Error("usage: capture.ts --url <served url> --log <event log> [--out <dir>]");
}
const { url, log, out } = { url: values.url, log: values.log, out: resolve(values.out) };

// The previous set goes first, so a stale image is never taken for a fresh one.
async function clearPreviousSet(): Promise<void> {
  await mkdir(out, { recursive: true });
  for (const name of await readdir(out)) {
    if (name.endsWith(".png") || /^watch-.*\.json$/.test(name) || name === "detect.json")
      await rm(join(out, name));
  }
}

async function waitUntilReady(page: Page, seq: number): Promise<void> {
  await page.locator("main").waitFor({ state: "visible" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction((expected) => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    return (
      document.getElementById("root")?.dataset.seq === String(expected) &&
      (host?.oversightGraph?.nodeCenters().length ?? 0) > 0
    );
  }, seq);
  // Wait for a rendered frame, not a timed delay.
  await page.evaluate(
    () =>
      new Promise<void>((done) =>
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            done();
          }),
        ),
      ),
  );
}

async function main(): Promise<number> {
  const sourceLog = await readFile(resolve(log), "utf8");
  const liveLog = editingLog(sourceLog);
  const expectedSeq = lastSeq(sourceLog);
  stdout.write(`shots: waiting for fixture seq ${String(expectedSeq)} and the graph renderer\n`);
  await clearPreviousSet();
  await writeFile(join(out, "live-diff.jsonl"), liveLog);

  // This tiny player serves only the derived cut. Keep SSE open so the live-diff scene has the
  // same healthy connection as the full player's scenes, including reconnects by cursor.
  const player = createServer((request, response) => {
    const after = Number(
      new URL(request.url ?? "/", "http://localhost").searchParams.get("after") ?? 0,
    );
    streamReplay(response, liveLog, after);
  });
  player.listen(0, "127.0.0.1");
  await once(player, "listening");
  const address = player.address();
  if (!address || typeof address === "string") throw new Error("Editing player has no port");
  let browser: Browser | undefined;
  let failures = 0;
  try {
    browser = await chromium.launch();
    for (const job of planJobs(scenes, themes)) {
      if (job.skip !== null) {
        stdout.write(`shots: skipped ${job.file} (${job.skip})\n`);
        continue;
      }
      const scene = scenes.find((candidate) => candidate.name === job.scene);
      const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 });
      context.setDefaultTimeout(TIMEOUT_MS);
      const page = await context.newPage();
      const pageErrors: string[] = [];
      page.on("pageerror", (error) => pageErrors.push(error.message));
      try {
        // Each live-diff capture replays the derived cut, independently of the served proposal log.
        // Respect the transport cursor on reconnect so no later proposal can leak into the scene.
        if (scene?.log === "editing") {
          await page.route("**/events?*", (route) =>
            route.continue({
              url: `http://127.0.0.1:${String(address.port)}/events${new URL(route.request().url()).search}`,
            }),
          );
        }
        const target = new URL(url);
        if (job.edgeShape) target.searchParams.set("edge-shape", job.edgeShape);
        await page.goto(target.href, { waitUntil: "load" });
        await waitUntilReady(page, scene?.log === "editing" ? lastSeq(liveLog) : expectedSeq);
        await scene?.prepare?.(page);
        await page.evaluate((theme) => {
          document.documentElement.dataset.theme = theme;
        }, job.theme);
        if (pageErrors.length > 0) throw new Error(`page errors: ${pageErrors.join("; ")}`);
        const path = join(out, job.file);
        await page.screenshot({ path });
        const metrics = await page.evaluate(() => {
          const probe = document.querySelector<GraphProbeHost>(
            '[aria-label="Repository graph"]',
          )?.oversightGraph;
          if (!probe) throw new Error("Graph renderer missing");
          return probe.metrics();
        });
        await writeFile(
          join(out, job.file.replace(/\.png$/, ".json")),
          JSON.stringify(metrics, null, 2) + "\n",
        );
        stdout.write(
          `shots: ${job.file} label minimum ${metrics.minLabelPx.toFixed(2)} CSS px, ${metrics.edgeShape}\n`,
        );
        stdout.write(`shots: captured ${job.file} (${String((await stat(path)).size)} bytes)\n`);
      } catch (error) {
        failures += 1;
        stdout.write(`shots: FAILED ${job.file}: ${String(error)}\n`);
      } finally {
        await context.close();
      }
    }
  } finally {
    try {
      await browser?.close();
    } finally {
      player.closeAllConnections();
      await new Promise<void>((done) =>
        player.close(() => {
          done();
        }),
      );
    }
  }
  return failures === 0 ? 0 : 1;
}

exit(await main());
