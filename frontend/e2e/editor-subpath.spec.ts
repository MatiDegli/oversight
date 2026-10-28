import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { Manifest } from "vite";
import type { GraphProbeHost } from "../src/graph";
import { recordEditorStartup } from "./editor-startup-support";

const manifest = JSON.parse(
  readFileSync(new URL("../dist/replay/oversight/.vite/manifest.json", import.meta.url), "utf8"),
) as Manifest;
const entry = manifest["src/editor/implementation.ts"]?.file;
if (!entry) throw new Error("Missing deferred editor in the subpath build");

function outsideBase(url: string, origin: string): boolean {
  const resource = new URL(url);
  return resource.origin !== origin || !resource.pathname.startsWith("/oversight/");
}

test("the subpath predicate rejects unowned blobs and paths outside the base", () => {
  const origin = "http://localhost:5710";
  for (const url of [
    "blob:http://localhost:5710/unowned",
    `${origin}/outside.js`,
    "https://other.test/oversight/a.js",
  ]) {
    expect(outsideBase(url, origin)).toBe(true);
  }
  expect(outsideBase(`${origin}/oversight/assets/app.js`, origin)).toBe(false);
});

test("the real /oversight/ replay prefetches once and keeps every request under its base", async ({
  page,
  baseURL,
}) => {
  const requested: string[] = [];
  const errors: string[] = [];
  const blobChecks: Promise<{ url: string; source: string | undefined }>[] = [];
  page.on("request", (request) => requested.push(request.url()));
  page.on("request", (request) => {
    const url = request.url();
    if (!url.startsWith("blob:")) return;
    blobChecks.push(
      page.evaluate((url) => {
        const source = document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.imageSource(url);
        return { url, source };
      }, url),
    );
  });
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
  // Network assets retain the strict deployment-base gate. Each local blob is checked
  // separately against the graph cache, rather than allowing arbitrary same-origin blobs.
  expect(
    requested.filter((url) => !url.startsWith("blob:")).filter((url) => outsideBase(url, origin)),
  ).toEqual([]);
  expect(blobChecks.length).toBeGreaterThan(0);
  for (const { url, source } of await Promise.all(blobChecks)) {
    expect(new URL(url).origin).toBe(origin);
    expect(source, `Blob must belong to the graph SVG cache: ${url}`).toMatch(
      /^data:image\/svg\+xml,/,
    );
  }
  expect(
    await page.evaluate(() => {
      const url = URL.createObjectURL(new Blob(["unowned"], { type: "image/svg+xml" }));
      try {
        return (
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.imageSource(url) === url
        );
      } finally {
        URL.revokeObjectURL(url);
      }
    }),
  ).toBe(true);
  expect(await page.evaluate(() => window.editorStartup)).toMatchObject({
    idleScheduled: 1,
    idleFired: 1,
    idleAfterGraph: true,
  });
  expect(errors).toEqual([]);
});
