import { launchBrowser } from "../browserLaunch.support";
import { fileURLToPath } from "node:url";
import { expect as browserExpect } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { fixtureCacheDir } from "../../shots/browserCache.support";

let server: ViteDevServer, browser: Browser, url: string, html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fixtureCacheDir(expect.getState().testPath),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    `<!doctype html><html><body><div id="root"></div><script type="module">
      import { createElement } from "react";
      import { createRoot } from "react-dom/client";
      import { deferEditor } from "/src/editor/lazyEditor.tsx";
      const error = new Error("Local editor chunk failed");
      const reports = [];
      const nativeError = console.error.bind(console);
      console.error = (...args) => {
        if (args[0] instanceof Error) reports.push(args);
        else nativeError(...args);
      };
      function BrokenEditor() { throw error; }
      const View = deferEditor(() => BrokenEditor, location.search.includes("file"));
      // Suppress React's separate default report to isolate the boundary's own diagnostic.
      const root = createRoot(document.getElementById("root"), { onCaughtError() {} });
      root.render(createElement(View));
      window.diagnostics = () => reports.map(args => ({
        count: args.length, sameError: args[0] === error, stack: args[1]
      }));
    </script></body></html>`,
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each([false, true])(
  "reports the error and component stack once (file panel: %s)",
  async (file) => {
    const page = await browser.newPage();
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("**/src/editor/loader.ts", (route) =>
        route.fulfill({
          contentType: "application/javascript",
          body: "export const loadedEditor = () => ({}); export const loadEditor = async () => ({});",
        }),
      );
      await page.route(
        (target) => target.pathname === "/",
        (route) => route.fulfill({ contentType: "text/html", body: html }),
      );
      await page.goto(`${url}${file ? "?file" : ""}`);
      await browserExpect(page.getByRole("alert")).toHaveText(
        "The editor could not load. Reload the page to try again.",
      );
      await browserExpect(page.getByRole("complementary", { name: "File view" })).toHaveCount(
        file ? 1 : 0,
      );
      const reports: unknown = await page.evaluate("window.diagnostics()");
      expect(reports).toEqual([
        { count: 2, sameError: true, stack: expect.stringContaining("BrokenEditor") as unknown },
      ]);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
);
