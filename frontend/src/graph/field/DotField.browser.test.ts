import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { fixtureCacheDir } from "../../../shots/browserCache.support";
import { launchBrowser } from "../../browserLaunch.support";

let server: ViteDevServer;
let browser: Browser;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    cacheDir: fixtureCacheDir(expect.getState().testPath),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    `<!doctype html><html><body><div id="root"></div><button id="cleanup">Unmount</button>
    <script type="module">
      import { createElement } from "react";
      import { createRoot } from "react-dom/client";
      import { DotField } from "/src/graph/field/DotField.tsx";
      const source = { current: {
        attach() {}, detach() {}, boxes() { return new Map(); }, subscribe() { return () => {}; }
      } };
      const root = createRoot(document.getElementById("root"));
      root.render(createElement(DotField, { source, mode: "watch", lights: [] }));
      document.getElementById("cleanup").onclick = () => root.unmount();
    </script></body></html>`,
  );
  await server.listen();
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it("mounts and cleans up when the optional controller import fails", async () => {
  const page = await browser.newPage();
  const errors: string[] = [];
  const intercepted: string[] = [];
  const controller = "/src/graph/field/controller.ts";
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    window.addEventListener("unhandledrejection", () => {
      document.body.dataset.unhandled = "true";
    });
  });
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.route(`**${controller}*`, (route) => {
      intercepted.push(route.request().url());
      return route.abort();
    });
    const failed = page.waitForEvent(
      "requestfailed",
      (r) => new URL(r.url()).pathname === controller,
    );
    await page.goto(server.resolvedUrls?.local[0] ?? "");
    await failed;
    await check(page.locator("#root canvas")).toHaveCount(1);
    // A second rejected import and two task turns let rejection events settle.
    expect(
      await page.evaluate(
        `import(${JSON.stringify(intercepted[0])}).then(() => false, () => true)`,
      ),
    ).toBe(true);
    await page.evaluate(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(intercepted.map((url) => new URL(url).pathname)).toEqual([controller, controller]);
    await page.locator("#cleanup").click();
    await check(page.locator("#root")).toBeEmpty();
    expect(await page.locator("body").getAttribute("data-unhandled")).toBeNull();
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
});
