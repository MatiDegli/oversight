import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";

let browser: Browser;
const servers: ViteDevServer[] = [];
const entries: { url: string; html: string }[] = [];
const refusal = "Rules mode is closed until Revert, protected paths and follow-main land";
beforeAll(async () => {
  for (const mode of ["development", "replay"]) {
    const server = await createServer({
      root: fileURLToPath(new URL("../../", import.meta.url)),
      mode,
      cacheDir: fileURLToPath(new URL(`../../../.shots/rules-${mode}-cache`, import.meta.url)),
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    servers.push(server);
    const html = await server.transformIndexHtml(
      "/",
      `<!doctype html><html><body><div id="root"></div><script type="module">import "/src/theme/index.css"; import "/src/app/settings.browser.fixture.tsx";</script></body></html>`,
    );
    await server.listen();
    entries.push({ url: server.resolvedUrls?.local[0] ?? "", html });
  }
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await Promise.all(servers.map((s) => s.close()));
});

async function open(replay = false, width = 1920, height = 1080, theme = "dark") {
  const entry = entries[replay ? 1 : 0];
  if (!entry) throw new Error("Missing server");
  const page = await browser.newPage({ viewport: { width, height } });
  await page.route("**/", (r) => r.fulfill({ contentType: "text/html", body: entry.html }));
  await page.route("**/events?*", (r) =>
    r.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" }),
  );
  await page.goto(entry.url);
  await check(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
  await page.evaluate((theme) => {
    document.documentElement.dataset.theme = theme;
  }, theme);
  return page;
}
async function mode(page: Page, mode: "ask_me" | "merge_when_safe", revision: number) {
  await page.evaluate(
    ({ mode, revision }) => {
      const store = (window as unknown as { appStore: typeof import("../state").stateStore })
        .appStore;
      const state = store.getState().state;
      store.getState().applyEvents([
        {
          seq: state.lastSeq + 1,
          ts: state.clock ?? "2026-10-31T17:00:00Z",
          type: "merge_mode.changed",
          payload: { mode, revision },
        },
      ]);
    },
    { mode, revision },
  );
}
const rules = (page: Page) =>
  page.getByRole("button", { name: "Merge alone when safe", exact: false });
async function shot(page: Page, name: string, suffix: string) {
  if (process.env.RULES_UI_SHOTS !== "1") return;
  const directory = fileURLToPath(new URL("../../../.shots/rules-ui/", import.meta.url));
  await mkdir(directory, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${directory}${name}-${suffix}.png` });
}

it("opens Settings only on demand, preserves focus, and sends one observed-revision PUT", async () => {
  const page = await open();
  page.setDefaultTimeout(5000);
  const requests: unknown[] = [];
  await page.route("**/merge-mode", (r) => {
    requests.push(r.request().postDataJSON());
    expect(r.request().method()).toBe("PUT");
    return r.fulfill({ status: 409, json: { error: { code: "wrong_state", message: refusal } } });
  });
  try {
    const gear = page.getByRole("button", { name: "Settings", exact: true });
    await gear.focus();
    await mode(page, "ask_me", 3);
    await check(gear).toBeFocused();
    await check(page.getByRole("tab", { name: "Settings", exact: true })).toHaveCount(0);
    await gear.click();
    await check(page.getByRole("tab", { name: "Settings", exact: true })).toHaveCount(1);
    const ask = page.getByRole("button", { name: "Ask me Every change", exact: false });
    await ask.click();
    await ask.focus();
    await page.keyboard.press("Tab");
    await check(rules(page)).toBeFocused();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");
    await rules(page).dispatchEvent("click", { detail: 0 });
    expect(requests).toEqual([]);
    await rules(page).click();
    await check(page.getByRole("alert").filter({ hasText: refusal })).toBeVisible();
    expect(requests).toEqual([{ mode: "merge_when_safe", revision: 3 }]);
    await page.getByRole("button", { name: /Open decisions inbox/ }).click();
    await gear.click();
    await check(page.getByRole("alert").filter({ hasText: refusal })).toBeVisible();
    await mode(page, "merge_when_safe", 4);
    await check(page.getByRole("alert").filter({ hasText: refusal })).toHaveCount(0);
    await page.getByRole("button", { name: "Close tab Settings", exact: true }).click();
    await page.getByRole("button", { name: "Close tab Decisions", exact: true }).click();
    await page.getByRole("button", { name: "Review change", exact: true }).click();
    await check(page.getByRole("button", { name: "Approve and merge", exact: true })).toBeVisible();
    expect(requests).toHaveLength(1);
  } finally {
    await page.close();
  }
});

it("waits for the event revision after a 200 and never marks the response optimistically", async () => {
  const page = await open();
  await page.route("**/merge-mode", (r) =>
    r.fulfill({ json: { mode: "merge_when_safe", revision: 1 } }),
  );
  try {
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await rules(page).click();
    await check(rules(page)).toBeDisabled();
    await check(rules(page)).toHaveAttribute("aria-pressed", "false");
    await mode(page, "merge_when_safe", 1);
    await check(rules(page)).toBeEnabled();
    await check(rules(page)).toHaveAttribute("aria-pressed", "true");
  } finally {
    await page.close();
  }
});

it.each([
  [1920, 1080],
  [1280, 800],
])(
  "captures approved states at %ix%i in both themes",
  async (width, height) => {
    for (const theme of ["dark", "day"]) {
      const page = await open(false, width, height, theme);
      const suffix = `${String(width)}x${String(height)}-${theme}`;
      try {
        await check(page.getByLabel("Status line")).toContainText("Merge mode: Ask me");
        await shot(page, "mode-status-ask", suffix);
        await mode(page, "merge_when_safe", 1);
        await check(page.getByLabel("Status line")).toContainText(
          "Merge mode: Merge alone when safe",
        );
        await shot(page, "mode-status-rules", suffix);
        await mode(page, "ask_me", 2);
        await page.getByRole("button", { name: "Settings", exact: true }).click();
        await shot(page, "mode-settings-ask", suffix);
        const request = page.waitForRequest("**/merge-mode");
        let release: (() => void) | undefined;
        await page.route("**/merge-mode", async (r) => {
          await new Promise<void>((resolve) => {
            release = resolve;
          });
          await r.fulfill({
            status: 409,
            json: { error: { code: "wrong_state", message: refusal } },
          });
        });
        await rules(page).click();
        await request;
        await check(rules(page)).toBeDisabled();
        await shot(page, "mode-settings-pending", suffix);
        release?.();
        await check(page.getByRole("alert").filter({ hasText: refusal })).toBeVisible();
        await shot(page, "mode-settings-refused", suffix);
      } finally {
        await page.close();
      }
      const replay = await open(true, width, height, theme);
      const requests: string[] = [];
      replay.on("request", (r) => {
        if (r.url().endsWith("/merge-mode")) requests.push(r.method());
      });
      try {
        await replay.getByRole("button", { name: "Settings", exact: true }).click();
        await check(
          replay.getByText("Setting the merge mode is not available in a replay."),
        ).toBeVisible();
        await rules(replay).click({ force: true });
        await rules(replay).focus();
        await replay.keyboard.press("Enter");
        await replay.keyboard.press("Space");
        expect(requests).toEqual([]);
        await shot(replay, "mode-settings-replay", suffix);
      } finally {
        await replay.close();
      }
    }
  },
  60_000,
);
