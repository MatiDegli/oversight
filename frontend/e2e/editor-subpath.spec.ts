import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { Manifest } from "vite";
import { recordEditorStartup } from "./editor-startup-support";

const manifest = JSON.parse(
  readFileSync(new URL("../dist/replay/oversight/.vite/manifest.json", import.meta.url), "utf8"),
) as Manifest;
const entry = manifest["src/editor/implementation.ts"]?.file;
if (!entry) throw new Error("Missing deferred editor in the subpath build");

test("the real /oversight/ replay prefetches once and keeps every request under its base", async ({
  page,
  baseURL,
}) => {
  const requested: string[] = [];
  const errors: string[] = [];
  page.on("request", (request) => requested.push(request.url()));
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(recordEditorStartup);
  await page.goto("/oversight/");
  await expect
    .poll(() => requested.filter((url) => new URL(url).pathname === `/oversight/${entry}`).length)
    .toBe(1);
  const timing = await page.evaluate(() => {
    const firstGraphAt = window.editorStartup.firstGraphAt;
    return {
      firstGraphAt,
      firstEditor: performance
        .getEntriesByType("resource")
        .find((entry) => entry.name.includes("/implementation-"))?.startTime,
    };
  });
  expect(timing.firstGraphAt).toBeGreaterThan(0);
  expect(timing.firstEditor).toBeGreaterThanOrEqual(timing.firstGraphAt);
  const origin = new URL(baseURL ?? "").origin;
  expect(
    requested.filter((url) => {
      const resource = new URL(url);
      // The graph's cached SVG layers use local blobs, not requests under the deployment base.
      return (
        resource.origin !== origin ||
        (resource.protocol !== "blob:" && !resource.pathname.startsWith("/oversight/"))
      );
    }),
  ).toEqual([]);
  expect(await page.evaluate(() => window.editorStartup)).toMatchObject({
    idleScheduled: 1,
    idleFired: 1,
    idleAfterGraph: true,
  });
  expect(errors).toEqual([]);
});
