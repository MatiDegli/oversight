import { readFileSync } from "node:fs";

import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { Manifest } from "vite";
import type { GraphProbeHost } from "../src/graph";

const manifest = JSON.parse(
  readFileSync(new URL("../dist/replay/.vite/manifest.json", import.meta.url), "utf8"),
) as Manifest;
export const editorAssets = new Set(
  Object.entries(manifest)
    .filter(([key]) => key.startsWith("src/editor/") || key.includes("monaco-editor"))
    .flatMap(([, chunk]) => [chunk.file, ...(chunk.css ?? []), ...(chunk.assets ?? [])])
    .map((file) => `/${file}`),
);
const deferredEntry = manifest["src/editor/implementation.ts"]?.file;
if (!deferredEntry) throw new Error("The built site must have one deferred editor entry");
export const implementation = deferredEntry;

/** Monaco can paint plain text while its separate, lazy Python grammar is still cold. */
export async function holdPythonGrammar(page: Page) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(/\/python-.*\.js$/, async (route) => {
    await gate;
    await route.continue();
  });
  // Release on an earlier assertion failure so routing cannot strand teardown.
  page.once("close", release);
  return async () => {
    const loaded = page.waitForResponse(/\/python-.*\.js$/, { timeout: 60_000 });
    release();
    const response = await loaded;
    expect(response.ok()).toBe(true);
    expect(await response.finished()).toBeNull();
  };
}

declare global {
  interface Window {
    editorStartup: {
      firstGraphAt: number;
      idleScheduled: number;
      idleFired: number;
      idleAfterGraph: boolean;
    };
  }
}

/** Observe a real Cytoscape canvas draw, independently of the app's readiness signal. */
export function recordEditorStartup() {
  const probe = { firstGraphAt: 0, idleScheduled: 0, idleFired: 0, idleAfterGraph: true };
  window.editorStartup = probe;
  // eslint-disable-next-line @typescript-eslint/unbound-method -- Reflect.apply preserves each canvas's receiver.
  const draw = CanvasRenderingContext2D.prototype.drawImage;
  CanvasRenderingContext2D.prototype.drawImage = function (...args: unknown[]) {
    const host = this.canvas.closest<GraphProbeHost>('[aria-label="Repository graph"]');
    Reflect.apply(draw, this, args);
    if (probe.firstGraphAt === 0 && (host?.oversightGraph?.nodeCenters().length ?? 0) > 0)
      probe.firstGraphAt = performance.now();
  };
  const idle =
    typeof window.requestIdleCallback === "function"
      ? window.requestIdleCallback.bind(window)
      : undefined;
  if (idle)
    window.requestIdleCallback = (callback, options) => {
      if (options?.timeout !== 2_000) return idle(callback, options);
      probe.idleScheduled += 1;
      probe.idleAfterGraph &&= probe.firstGraphAt > 0;
      return idle((deadline) => {
        probe.idleFired += 1;
        callback(deadline);
      }, options);
    };
}

export async function expectNoEditorBeforeGraph(page: Page) {
  const timing = await page.evaluate(
    (assets) => {
      const firstGraphAt = window.editorStartup.firstGraphAt;
      return {
        firstGraphAt,
        early: performance
          .getEntriesByType("resource")
          .filter(
            (entry) =>
              assets.includes(new URL(entry.name).pathname) && entry.startTime < firstGraphAt,
          )
          .map((entry) => entry.name),
      };
    },
    [...editorAssets],
  );
  expect(timing.firstGraphAt).toBeGreaterThan(0);
  expect(timing.early).toEqual([]);
}
