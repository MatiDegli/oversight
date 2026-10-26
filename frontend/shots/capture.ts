// Captures 1920x1080 PNGs of the served UI into .shots/, one per enabled scene and theme, named
// `<scene>-<theme>[-<edge-shape>].png`, replacing the previous set. Started by make shots, which serves
// the replay and Vite first:
//
//   node shots/capture.ts --url http://127.0.0.1:5711/ --log ../fixtures/events/x.jsonl --out ../.shots
import { mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { exit, stdout } from "node:process";
import { parseArgs } from "node:util";

import { chromium } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { Core } from "cytoscape";

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
    if (name.endsWith(".png") || /^watch-.*\.json$/.test(name) || name === "detect.json")
      await rm(join(out, name));
  }
}

// Cytoscape registers its instance on the host; inspect it only in this development capture tool.
declare global {
  interface HTMLElement {
    _cyreg?: { cy?: Core };
  }
}

async function waitUntilReady(page: Page, seq: number): Promise<void> {
  await page.locator("main").waitFor({ state: "visible" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction((expected) => {
    const host = document.querySelector<HTMLElement>('[aria-label="Repository graph"]');
    return (
      document.getElementById("root")?.dataset.seq === String(expected) &&
      (host?._cyreg?.cy?.nodes().length ?? 0) > 0
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
  const expectedSeq = lastSeq(await readFile(resolve(log), "utf8"));
  stdout.write(`shots: waiting for fixture seq ${String(expectedSeq)} and the graph renderer\n`);
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
        const target = new URL(url);
        if (job.edgeShape) target.searchParams.set("edge-shape", job.edgeShape);
        await page.goto(target.href, { waitUntil: "load" });
        await waitUntilReady(page, expectedSeq);
        await scene?.prepare?.(page);
        await page.evaluate((theme) => {
          document.documentElement.dataset.theme = theme;
        }, job.theme);
        if (pageErrors.length > 0) throw new Error(`page errors: ${pageErrors.join("; ")}`);
        const path = join(out, job.file);
        await page.screenshot({ path });
        const metrics = await page.evaluate(() => {
          const cy = document.querySelector<HTMLElement>('[aria-label="Repository graph"]')?._cyreg
            ?.cy;
          if (!cy) throw new Error("Graph renderer missing");
          return {
            viewport: { width: cy.width(), height: cy.height() },
            zoom: cy.zoom(),
            minLabelPx: Math.min(
              ...cy.nodes().map((node) => node.numericStyle("font-size") * cy.zoom()),
            ),
            edgeShape: String(cy.edges().first().style("curve-style")),
          };
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
    await browser.close();
  }
  return failures === 0 ? 0 : 1;
}

exit(await main());
