import type { EditableSetting, SettingsView } from "@contracts";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../../browserLaunch.support";
import type {} from "./settingsEditor.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    mode: "replay",
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/shell/settingsEditor.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

const settings: EditableSetting[] = [
  { name: "META_API_KEY", kind: "secret", state: "set", value: null, overridden: false },
  { name: "BASETEN_API_KEY", kind: "secret", state: "missing", value: null, overridden: false },
  {
    name: "OVERSIGHT_CROSS_REVIEW",
    kind: "boolean",
    state: "set",
    value: "false",
    overridden: false,
  },
  ...(
    [
      "OVERSIGHT_BUDGET_USD",
      "OVERSIGHT_BASETEN_BUDGET_USD",
      "MUSE_SPARK_INPUT_USD_PER_M",
      "MUSE_SPARK_OUTPUT_USD_PER_M",
      "BASETEN_INPUT_USD_PER_M",
      "BASETEN_OUTPUT_USD_PER_M",
      "OVERSIGHT_MAX_AGENTS",
    ] as const
  ).map((name): EditableSetting => ({
    name,
    kind: "number",
    state: "set",
    value: "4",
    overridden: false,
  })),
];
it.each([1280, 390])(
  "edits, preserves refused drafts and scrolls without overflow at %ipx",
  async (width) => {
    const page = await browser.newPage({
      viewport: { width, height: 800 },
      reducedMotion: "reduce",
    });
    const patches: unknown[] = [];
    const view: SettingsView = { settings, restart_required: false, apply: "server_restart" };
    await page.route("**/settings", async (route) => {
      if (route.request().method() === "PUT") {
        patches.push(route.request().postDataJSON());
        if (patches.length === 1)
          return route.fulfill({
            status: 422,
            json: {
              error: { code: "invalid_request", message: "Invalid settings: META_API_KEY." },
            },
          });
        return route.fulfill({ json: { ...view, restart_required: true } });
      }
      return route.fulfill({ json: view });
    });
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    try {
      await page.goto(url);
      const opener = page.getByRole("button", { name: "Settings", exact: true });
      const bounds = await opener.boundingBox();
      expect(bounds && bounds.x + bounds.width).toBeLessThanOrEqual(width);
      await opener.click();
      const models = page.getByRole("region", { name: "Models and keys" });
      const key = models.getByLabel("Meta API key", { exact: true });
      await check(key).toBeEditable();
      await check(key).toHaveValue("");
      await key.fill("synthetic-replacement");
      await models.getByRole("button", { name: "Save", exact: true }).click();
      await check(page.getByRole("alert")).toHaveText("Invalid settings: META_API_KEY.");
      await check(key).toHaveValue("synthetic-replacement");
      await models.getByRole("button", { name: "Save", exact: true }).click();
      await check(models.getByRole("status")).toHaveText("Saved.");
      await check(key).toHaveValue("");
      expect(patches).toEqual(
        Array.from({ length: 2 }, () => ({ values: { META_API_KEY: "synthetic-replacement" } })),
      );
      const limits = page.getByRole("region", { name: "Limits and budget" });
      const cap = limits.getByLabel("Agent cap (agents)");
      await cap.fill("8");
      await limits.getByRole("button", { name: "Save", exact: true }).click();
      await check(page.getByText("Restart the server to apply.", { exact: true })).toBeVisible();
      expect(patches[2]).toEqual({ values: { OVERSIGHT_MAX_AGENTS: "8" } });
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      const overflow = await page
        .locator("input")
        .evaluateAll((inputs) =>
          inputs.some((input) => input.getBoundingClientRect().right > innerWidth),
        );
      expect(overflow).toBe(false);
      await page.getByRole("heading", { name: "Models and keys" }).scrollIntoViewIfNeeded();
      await page.evaluate(() => document.fonts.ready);
      await mkdir("../.shots/settings-editor", { recursive: true });
      await page.screenshot({ path: `../.shots/settings-editor/${String(width)}.png` });
    } finally {
      await page.close();
    }
  },
);
it("keeps replay settings read-only at 390px and never requests the settings file", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 800 } });
  const requests: string[] = [];
  await page.route("**/settings", (route) => {
    requests.push(route.request().url());
    return route.abort();
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  try {
    await page.goto(url);
    await page.waitForFunction(() => !!window.settingsReplay);
    await page.evaluate(() => {
      window.settingsReplay();
    });
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await check(page.getByText("Settings are read-only in a replay.")).toBeVisible();
    await check(page.getByLabel("Meta API key", { exact: true })).not.toBeEditable();
    const limits = page.getByRole("region", { name: "Limits and budget" });
    await check(limits.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    expect(requests).toEqual([]);
  } finally {
    await page.close();
  }
});
