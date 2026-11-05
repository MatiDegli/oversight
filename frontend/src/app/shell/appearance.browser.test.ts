import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../../browserLaunch.support";
import type {} from "./settingsEditor.browser.fixture";

// The page applies the stored preference before the fixture mounts, as main.tsx does.
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    mode: "replay",
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import { applyAppearance } from "/src/appearance.ts"; applyAppearance(); await import("/src/app/shell/settingsEditor.browser.fixture.tsx");</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function openSettings(colorScheme: "light" | "dark") {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, colorScheme });
  await page.route("**/settings", (route) =>
    route.fulfill({ json: { settings: [], restart_required: false, apply: "server_restart" } }),
  );
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  return page;
}

const shots = fileURLToPath(new URL("../../../../.shots/appearance/", import.meta.url));
const capture = async (page: Page, name: string) => {
  await mkdir(shots, { recursive: true });
  const section = page.getByRole("region", { name: "Appearance" });
  await section.scrollIntoViewIfNeeded();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${shots}${name}-1280.png` });
};

const theme = (page: Page) => page.evaluate(() => document.documentElement.dataset.theme);
const stored = (page: Page) =>
  page.evaluate(() => localStorage.getItem("oversight.appearance") ?? null);

it("starts dark on a light computer, applies and stores Day, and leaves focus with the person", async () => {
  const page = await openSettings("light");
  try {
    const group = page.getByRole("radiogroup", { name: "Appearance" });
    await check(group).toBeVisible();
    await check(page.getByText("Dark matches the recorded demo.", { exact: false })).toBeVisible();
    expect(await theme(page)).toBe("dark");
    await check(group.getByRole("radio", { name: "Dark" })).toBeChecked();
    const note = page.getByText("pick up the new colors when Oversight reloads", { exact: false });
    await check(note).toBeHidden();
    await capture(page, "dark");

    // Pointer: the click focuses the radio it hit, and nothing moves focus afterwards.
    await group.getByText("Day", { exact: true }).click();
    await check(group.getByRole("radio", { name: "Day" })).toBeChecked();
    expect(await theme(page)).toBe("day");
    expect(await stored(page)).toBe("day");
    await check(group.getByRole("radio", { name: "Day" })).toBeFocused();
    await check(note).toBeVisible();
    await page.mouse.move(0, 0);
    await capture(page, "day");
    // The hidden radios sit inside their labels: revealing the group scrolls only the Settings
    // panel, never a clipped ancestor that would shift the whole window.
    const shifted = await group.evaluate((element) => {
      const moved: string[] = [];
      for (let node = element.parentElement; node; node = node.parentElement) {
        const scrolls = /auto|scroll/u.test(getComputedStyle(node).overflowY);
        if (!scrolls && node.scrollTop !== 0) moved.push(node.tagName + "#" + node.id);
      }
      return moved;
    });
    expect(shifted).toEqual([]);

    // Keyboard: the native arrow key selects System, which follows the light computer.
    await page.keyboard.press("ArrowRight");
    await check(group.getByRole("radio", { name: "System" })).toBeChecked();
    await check(group.getByRole("radio", { name: "System" })).toBeFocused();
    const ring = await group
      .locator("label")
      .filter({ hasText: "System" })
      .evaluate((label) => getComputedStyle(label).outlineStyle);
    expect(ring).toBe("solid");
    expect(await stored(page)).toBe("system");
    expect(await theme(page)).toBe("day");

    // While System is selected, the computer's change applies at once.
    await page.emulateMedia({ colorScheme: "dark" });
    await check.poll(() => theme(page)).toBe("dark");
    await check(note).toBeHidden();
    await check(group.getByRole("radio", { name: "System" })).toBeFocused();

    // After a reload the stored choice paints first, so no note is needed.
    await page.reload();
    expect(await theme(page)).toBe("dark");
    await page.emulateMedia({ colorScheme: "light" });
    await check.poll(() => theme(page)).toBe("day");
  } finally {
    await page.close();
  }
});

it("reads an invalid stored value as dark", async () => {
  const page = await openSettings("light");
  try {
    await page.evaluate(() => {
      localStorage.setItem("oversight.appearance", "light");
    });
    await page.reload();
    expect(await theme(page)).toBe("dark");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await check(
      page.getByRole("radiogroup", { name: "Appearance" }).getByRole("radio", { name: "Dark" }),
    ).toBeChecked();
  } finally {
    await page.close();
  }
});
