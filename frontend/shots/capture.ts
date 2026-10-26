// Captures 1920x1080 PNGs of the served UI into .shots/, one per enabled scene and theme, named
// `<scene>-<theme>.png`, replacing the previous set. Started by `make shots` (run.sh), which serves
// the replay and Vite first:
//
//   node shots/capture.ts --url http://127.0.0.1:5711/ --log ../fixtures/events/x.jsonl --out ../.shots
import { mkdir, readdir, readFile, rm, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { exit, stdout } from "node:process";
import { parseArgs } from "node:util";

import { chromium } from "@playwright/test";
import type { Page } from "@playwright/test";

import { lastSeq, planJobs } from "./plan.ts";
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
    if (name.endsWith(".png") || name === "detect.json") await rm(join(out, name));
  }
}

// What "the replay applied everything" means for the page.
// TODO(transport front): main has no event transport yet, so the app reads no /events and no seq
// reaches the page. When it does, have the app set `data-seq` on the root element from the store's
// last applied seq, and wait here for it to equal the fixture's last seq (`expectedSeq` in main(); a test in src/ covers the
// attribute). Until then the shell rendering is the only readiness there is.
async function waitUntilReady(page: Page): Promise<void> {
  await page.locator("main").waitFor({ state: "visible" });
  await page.evaluate(() => document.fonts.ready);
}

async function main(): Promise<number> {
  const expectedSeq = lastSeq(await readFile(resolve(log), "utf8"));
  stdout.write(
    `shots: fixture ${log} ends at seq ${String(expectedSeq)}; the UI reads no events yet, ` +
      "so each scene waits for the shell to render\n",
  );
  await clearPreviousSet();

  const browser = await chromium.launch();
  let failures = 0;
  try {
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
        await page.goto(url, { waitUntil: "load" });
        await waitUntilReady(page);
        await scene?.prepare?.(page);
        await page.evaluate((theme) => {
          document.documentElement.dataset.theme = theme;
        }, job.theme);
        if (pageErrors.length > 0) throw new Error(`page errors: ${pageErrors.join("; ")}`);
        const path = join(out, job.file);
        await page.screenshot({ path });
        stdout.write(`shots: captured ${job.file} (${String((await stat(path)).size)} bytes)\n`);
      } catch (error) {
        failures += 1;
        stdout.write(`shots: FAILED ${job.file}: ${String(error)}\n`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
  return failures === 0 ? 0 : 1;
}

exit(await main());
