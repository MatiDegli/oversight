import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";

let server: ViteDevServer, browser: Browser, url: string, cacheDir: string;
const harness = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { useMergeArrival } from '/src/app/useMergeArrival.ts';
import { applyEvent, createInitialState } from '/src/state/index.ts';
import { graphElements } from '/src/graph/index.ts';
import raw from '/@fs/${fileURLToPath(new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url))}?raw';
const events = raw.trim().split('\\n').map(JSON.parse);
const before = events.filter(e => e.seq <= 20).reduce(applyEvent, createInitialState());
const merged = applyEvent(before, events.find(e => e.seq === 21));
const records = [];
function Consumer({ state }) {
  const arrival = useMergeArrival(state);
  // Record in the consumer's render, before any layout effect can repair the presentation.
  if (state === merged) {
    const row = graphElements(state, false, null, null, false, null, arrival.merging)
      .find(row => row.data.id === 'shop.pricing:compute_price');
    records.push({ label: arrival.labels.get('change-1'), merging: [...arrival.merging],
      word: row?.data.marks?.word, classes: row?.classes });
  }
  return null;
}
const root = createRoot(document.getElementById('root'));
window.arrivalRender = {
  mount() { flushSync(() => root.render(React.createElement(Consumer, { state: before }))); },
  merge() { flushSync(() => root.render(React.createElement(Consumer, { state: merged }))); },
  records: () => records
};`;
declare global {
  interface Window {
    arrivalRender: {
      mount(): void;
      merge(): void;
      records(): { label?: string; merging: string[]; word?: string; classes?: string }[];
    };
  }
}
beforeAll(async () => {
  // Optimized dependencies contain checkout paths, so each run needs its own cache.
  cacheDir = await mkdtemp(join(tmpdir(), "oversight-arrival-render-"));
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir,
    server: { host: "127.0.0.1", port: 0 },
  });
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
});
it("returns Merging and holds the pending mark in the first render receiving a merge", async () => {
  const page = await browser.newPage({ reducedMotion: "no-preference" });
  try {
    const html = await server.transformIndexHtml(
      "/",
      `<html><body><div id="root"></div><script type="module">${harness}</script></body></html>`,
    );
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.arrivalRender);
    await page.evaluate(() => {
      window.arrivalRender.mount();
    });
    await page.evaluate(() => {
      window.arrivalRender.merge();
    });
    const [first] = await page.evaluate(() => window.arrivalRender.records());
    expect(first?.label).toBe("Merging");
    expect(first?.merging).toEqual(["change-1"]);
    expect(first?.word).toBe("changed");
    expect(first?.classes).toContain("pending");
  } finally {
    await page.close();
  }
});
