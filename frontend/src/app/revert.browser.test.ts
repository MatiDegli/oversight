import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";

declare global {
  interface Window {
    revertHarness: {
      merge: (person?: boolean) => void;
      outcome: (result: "reverted" | "needs_you") => void;
      inboxCount: () => number;
    };
    appStore: typeof import("../state").stateStore;
  }
}
let browser: Browser;
const entries: { server: ViteDevServer; html: string }[] = [];
const refusal = "Revert target is already reserved or consumed";
const revert = (page: Page) => page.getByRole("button", { name: "Revert", exact: true });
beforeAll(async () => {
  for (const mode of ["development", "replay"]) {
    const server = await createServer({
      root: fileURLToPath(new URL("../../", import.meta.url)),
      mode,
      cacheDir: fileURLToPath(new URL(`../../../.shots/revert-${mode}-cache`, import.meta.url)),
      plugins: [
        {
          name: "revert-replay-session",
          enforce: "pre",
          // Replay evidence is injected by the fixture; do not start the bundled recording too.
          resolveId(source, importer) {
            if (
              mode === "replay" &&
              source === "./session" &&
              importer?.endsWith("/src/app/App.tsx")
            )
              return "\0revert-replay-session";
            return null;
          },
          load(id) {
            if (id === "\0revert-replay-session")
              return "export function startAppSession() { return () => {}; }";
            return null;
          },
        },
        {
          name: "revert-connected-events",
          configureServer(server) {
            server.middlewares.use("/events", (_request, response) => {
              response.writeHead(200, {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
              });
              response.write(": connected\n\n");
            });
          },
        },
      ],
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    const html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html><body><div id="root"></div><script type="module">import "/src/theme/index.css"; import "/src/app/revert.browser.fixture.tsx";</script></body></html>',
    );
    await server.listen();
    entries.push({ server, html });
  }
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await Promise.all(entries.map(({ server }) => server.close()));
});
async function open(replay = false, width = 1920, height = 1080, theme = "dark") {
  const entry = entries[replay ? 1 : 0];
  if (!entry) throw new Error("Missing server");
  const page = await browser.newPage({ viewport: { width, height } });
  await page.route("**/", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: entry.html.replace("<html>", `<html data-theme="${theme}">`),
    }),
  );
  await page.route("**/files?**", async (route) => {
    const content = await page.evaluate(
      () =>
        window.appStore.getState().state.changes["change-1"]?.files["shop/pricing.py"]?.file
          .modified ?? "",
    );
    await route.fulfill({
      json: { path: "shop/pricing.py", agent_id: "agent-1", content, hash: "f".repeat(64) },
    });
  });
  await page.goto(entry.server.resolvedUrls?.local[0] ?? "");
  await page.getByRole("button", { name: "Review change", exact: true }).first().click();
  return page;
}
async function shot(page: Page, name: string, suffix: string) {
  const status = page.getByLabel("Status line");
  await check(status).not.toContainText("Connection lost");
  await check(status.getByRole("button", { name: "Retry connection" })).toHaveCount(0);
  if (name !== "revert-replay") await check(status).toContainText("Connected");
  if (process.env.RULES_UI_SHOTS !== "1") return;
  const directory = fileURLToPath(new URL("../../../.shots/rules-ui/", import.meta.url));
  await mkdir(directory, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  if (name !== "revert-needs-you-inbox")
    await check(page.locator(".monaco-diff-editor")).toBeVisible();
  await page.screenshot({ path: `${directory}${name}-${suffix}.png` });
}
it.each([
  [1920, 1080],
  [1280, 800],
])(
  "exercises and captures Revert at %ix%i in both themes",
  async function exerciseRevertStates(width, height) {
    for (const theme of ["dark", "day"]) {
      const page = await open(false, width, height, theme);
      const suffix = `${String(width)}x${String(height)}-${theme}`;
      const requests: unknown[] = [];
      let release: (() => void) | undefined;
      await page.route("**/changes/*/revert", async (route) => {
        expect(route.request().method()).toBe("POST");
        requests.push(route.request().postDataJSON());
        if (requests.length === 1) {
          await new Promise<void>((resolve) => {
            release = resolve;
          });
          await route.fulfill({
            status: 409,
            json: { error: { code: "wrong_state", message: refusal } },
          });
        } else await route.fulfill({ status: 202 });
      });
      try {
        await page.evaluate(() => {
          window.revertHarness.merge();
        });
        await check(revert(page)).toBeVisible();
        await check(page.locator(".review-merged")).toHaveText("Merged into main 4ca5596");
        await shot(page, "revert-ready", suffix);
        await revert(page).focus();
        await page.keyboard.press("Enter");
        await page.keyboard.press("Space");
        await revert(page).dispatchEvent("click", { detail: 0 });
        expect(requests).toHaveLength(0);
        const body = {
          resolution_seq: 20,
          merge_commit: "4ca5596e0d1b2c3a4f5e6d7c8b9a0f1e2d3c4b5a",
          main_commit: "ac712fde4410527789b142c4be06895ad18cbd12",
        };
        await revert(page).click();
        await check(revert(page)).toBeDisabled();
        await check(
          page.getByText("Reverting: waiting for the result.", { exact: true }),
        ).toHaveCount(1);
        await revert(page).dispatchEvent("click", { detail: 1 });
        await check(revert(page)).toBeFocused();
        await shot(page, "revert-pending", suffix);
        expect(requests).toEqual([body]);
        release?.();
        await check(page.getByRole("alert").filter({ hasText: refusal })).toHaveText(refusal);
        await check(revert(page)).toBeFocused();
        await shot(page, "revert-refused", suffix);
        // Attempts survive leaving review and returning to the same tab.
        await page.getByRole("button", { name: /Open decisions inbox/ }).click();
        await page.getByRole("tab", { name: /Review · change-1/ }).click();
        await check(page.getByRole("alert").filter({ hasText: refusal })).toHaveText(refusal);
        await revert(page).click();
        await check(revert(page)).toBeDisabled();
        const before = await page.evaluate(() => ({
          count: window.revertHarness.inboxCount(),
          tabs: document.querySelectorAll('[role="tab"]').length,
        }));
        await page.evaluate(() => {
          const active = document.activeElement;
          window.revertHarness.outcome("needs_you");
          if (document.activeElement !== active) throw new Error("Focus moved");
        });
        await check(revert(page)).toBeEnabled();
        await check(revert(page)).toBeFocused();
        await check(page.getByText("Main moved", { exact: true })).toHaveCount(1);
        expect(await page.evaluate(() => window.revertHarness.inboxCount())).toBe(before.count + 1);
        expect(await page.getByRole("tab").count()).toBe(before.tabs);
        await shot(page, "revert-needs-you", suffix);
        await page.getByRole("button", { name: /Open decisions inbox/ }).click();
        await check(page.getByRole("heading", { name: "Main moved", exact: true })).toBeVisible();
        await shot(page, "revert-needs-you-inbox", suffix);
        await page.getByRole("tab", { name: /Review · change-1/ }).click();
        await revert(page).click();
        await page.evaluate(() => {
          window.revertHarness.outcome("reverted");
        });
        await check(revert(page)).toHaveCount(0);
        await check(page.getByText("Reverted in main ba324d1", { exact: true })).toHaveCount(1);
        await check(page.locator(".review-merged")).toHaveText("Merged into main 4ca5596");
        expect(requests).toHaveLength(3);
        await shot(page, "revert-reverted", suffix);
      } finally {
        await page.close();
      }
      const replay = await open(true, width, height, theme);
      const sent: string[] = [];
      replay.on("request", (request) => {
        if (request.url().endsWith("/revert")) sent.push(request.method());
      });
      try {
        await replay.evaluate(() => {
          window.revertHarness.merge();
        });
        await check(
          replay.getByText("Revert is not available in a replay.", { exact: true }),
        ).toHaveCount(1);
        await check(revert(replay)).toHaveCount(0);
        expect(sent).toEqual([]);
        await shot(replay, "revert-replay", suffix);
      } finally {
        await replay.close();
      }
    }
  },
  60_000,
);
it("offers no Revert after a person-approved merge", async () => {
  const page = await open();
  try {
    await page.evaluate(() => {
      window.revertHarness.merge(true);
    });
    await check(page.getByText("Approved and merged", { exact: false })).toBeVisible();
    await check(revert(page)).toHaveCount(0);
  } finally {
    await page.close();
  }
});
