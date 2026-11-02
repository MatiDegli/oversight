import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./chatShell.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string, cacheDir: string;
const harness = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import '/src/app/chatShell.browser.fixture.tsx';
import { EditorDiff } from '/src/editor/index.ts';
import { loadedEditor } from '/src/editor/loader.ts';
window.startupEditor = {
  loaded: () => !!loadedEditor(),
  open() {
    const host = document.body.appendChild(document.createElement('div'));
    createRoot(host).render(React.createElement(EditorDiff, {
      data: { changeId: 'explicit', path: 'example.py', original: 'x = 1', modified: 'x = 2' },
      onMount() { host.dataset.mounted = 'true'; }
    }));
  }
};`;
declare global {
  interface Window {
    startupEditor: { loaded(): boolean; open(): void };
    startupIdle: { callbacks: IdleRequestCallback[]; options: (IdleRequestOptions | undefined)[] };
  }
}
beforeAll(async () => {
  // Optimized dependencies contain checkout paths: parallel checkouts must never share them.
  cacheDir = await mkdtemp(join(tmpdir(), "oversight-chat-startup-"));
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
    plugins: [
      {
        name: "chat-startup-transport",
        configureServer(vite) {
          vite.middlewares.use((req, res, next) => {
            if (!req.url?.startsWith("/events?")) {
              next();
              return;
            }
            res.setHeader("Content-Type", "text/event-stream");
            res.write(": connected\n\n");
          });
        },
      },
    ],
  });
  html = await server.transformIndexHtml(
    "/",
    `<html><body><div id="root"></div><script type="module">import '/src/theme/index.css'; ${harness}</script></body></html>`,
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
});

it.each(["explicit open", "idle prefetch"])(
  "unblocks %s in a fresh Chat-first App",
  async (mode) => {
    const page = await browser.newPage();
    const requested: string[] = [];
    page.on("request", (request) => requested.push(new URL(request.url()).pathname));
    await page.addInitScript(() => {
      window.startupIdle = { callbacks: [], options: [] };
      window.requestIdleCallback = (callback, options) => {
        window.startupIdle.callbacks.push(callback);
        window.startupIdle.options.push(options);
        return window.startupIdle.callbacks.length;
      };
      window.cancelIdleCallback = () => {};
    });
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    try {
      await page.goto(url);
      await page.waitForFunction(() => !!window.startupEditor);
      await page.evaluate(() => {
        window.chatShellTest.mount({ initial: "Chat", app: true });
      });
      const noGraph = async () => {
        await check(page.getByLabel("Repository graph", { exact: true })).toHaveCount(0);
        expect(
          requested.filter((path) =>
            /GraphView|DotField|cytoscape|\/field\/controller|OversightStage/.test(path),
          ),
        ).toEqual([]);
        expect(await page.evaluate(() => window.chatShellTest.contexts())).toBe(0);
      };
      await noGraph();
      expect(await page.evaluate(() => window.startupEditor.loaded())).toBe(false);
      expect(requested.some((path) => path.includes("/editor/implementation"))).toBe(false);
      if (mode === "explicit open") {
        await page.evaluate(() => {
          window.startupEditor.open();
        });
        await page.waitForFunction(() => !!document.querySelector('[data-mounted="true"]'));
      } else {
        await page.waitForFunction(() => window.startupIdle.callbacks.length === 1);
        expect(await page.evaluate(() => window.startupIdle.options)).toEqual([{ timeout: 2000 }]);
        expect(await page.evaluate(() => window.startupEditor.loaded())).toBe(false);
        expect(requested.some((path) => path.includes("/editor/implementation"))).toBe(false);
        await page.evaluate(() =>
          window.startupIdle.callbacks[0]?.({ didTimeout: false, timeRemaining: () => 50 }),
        );
      }
      await page.waitForFunction(() => window.startupEditor.loaded());
      expect(requested.some((path) => path.includes("/editor/implementation"))).toBe(true);
      await noGraph();
    } finally {
      await page.close();
    }
  },
);
