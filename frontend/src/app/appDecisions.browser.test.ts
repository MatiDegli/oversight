import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import { through } from "./replay.fixture";

let server: ViteDevServer;
let browser: Browser;
let html: string;
let url: string;

beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/app-decisions-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    `<!doctype html><html><body><div id="root"></div><script type="module">
      import "/src/theme/index.css";
      import { createRoot } from "react-dom/client";
      import { createElement } from "react";
      import { App } from "/src/app/App.tsx";
      import { stateStore } from "/src/state/index.ts";
      import log from "/@fs/${fileURLToPath(new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url))}?raw";
      stateStore.getState().applyEvents(log.trim().split("\\n").map(JSON.parse).filter(e => e.seq <= 20));
      window.appStore = stateStore;
      createRoot(document.getElementById("root")).render(createElement(App));
    </script></body></html>`,
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);

afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each([
  ["approve", "Approve and merge"],
  ["reject", "Reject"],
  ["request-changes", "Request changes"],
] as const)(
  "App sends the rendered target when %s races with a newer proposal",
  async (action, label) => {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      // Keep App's real transport idle: only the test advances the store.
      await page.route("**/events?*", (route) =>
        route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" }),
      );
      await page.route(`**/changes/change-1/${action}`, (route) => route.fulfill({ status: 202 }));
      await page.route("**/files?*", (route) => {
        const path = new URL(route.request().url()).searchParams.get("path") ?? "";
        const content = through(20).changes["change-1"]?.files[path]?.file.modified ?? "";
        return route.fulfill({
          json: {
            path,
            agent_id: "agent-1",
            content,
            hash: createHash("sha256").update(content).digest("hex"),
          },
        });
      });
      await page.goto(url);
      await page.getByRole("button", { name: "Review change", exact: true }).click();
      const panel = page.getByRole("complementary", { name: "Review panel" });
      await panel.getByRole("textbox", { name: /Comment/ }).fill("Cover empty carts");
      await browserExpect(panel.getByRole("button", { name: label, exact: true })).toBeEnabled();
      const sent = page.waitForRequest((request) =>
        request.url().endsWith(`/changes/change-1/${action}`),
      );
      const rendered = await page.evaluate((label) => {
        const store = (window as unknown as { appStore: typeof import("../state").stateStore })
          .appStore;
        const state = store.getState().state;
        const proposal = state.changes["change-1"]?.proposal;
        if (!proposal) throw new Error("Missing rendered proposal");
        const button = [...document.querySelectorAll<HTMLButtonElement>("button")].find(
          (item) => item.textContent.trim() === label,
        );
        if (!button) throw new Error("Missing decision button");
        store.getState().applyEvents([
          {
            seq: state.lastSeq + 1,
            ts: "2026-10-27T14:04:00.000Z",
            type: "change.proposed",
            payload: { ...proposal, revision: proposal.revision + 1, head_commit: "newer-head" },
          },
        ]);
        const latest = store.getState().state.changes["change-1"]?.proposal;
        if (latest?.revision !== proposal.revision + 1 || latest.head_commit !== "newer-head")
          throw new Error("The store did not advance before the click");
        // Same task: the store is newer, but React has not committed a new render yet.
        button.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
        return { revision: proposal.revision, head_commit: proposal.head_commit };
      }, label);
      expect((await sent).postDataJSON()).toEqual({ ...rendered, comment: "Cover empty carts" });
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  30_000,
);
