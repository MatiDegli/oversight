import { expect, test } from "@playwright/test";
import type { Locator } from "@playwright/test";

import type { GraphProbeHost } from "../src/graph";
import { themes } from "../src/theme";
import { engaged, installHolds, release } from "./holds";
import { holds } from "./level2-log";
import { interceptCommands, tapNode } from "./level2-support";
import {
  editorAssets,
  expectNoEditorBeforeGraph,
  holdPythonGrammar,
  recordEditorStartup,
} from "./editor-startup-support";

async function expectPython(panel: Locator) {
  // Read the painted keyword's color, rather than merely checking a language id or token class.
  await expect
    .poll(() =>
      panel.evaluate((host, keywordColor) => {
        const swatch = document.createElement("span");
        swatch.style.color = keywordColor;
        host.append(swatch);
        const expected = getComputedStyle(swatch).color;
        swatch.remove();
        return [...host.querySelectorAll(".view-line span")].some(
          (span) => span.textContent.trim() === "def" && getComputedStyle(span).color === expected,
        );
      }, themes.dark["syntax-keyword"]),
    )
    .toBe(true);
}

for (const first of ["file", "live diff", "review"] as const) {
  test(`no editor before first graph; ${first} still colors Python and shows its contents`, async ({
    page,
    baseURL,
  }) => {
    // Playwright gives each test a fresh context; request routing also disables the HTTP cache.
    const requested: string[] = [];
    const problems: string[] = [];
    page.on("request", (request) => requested.push(request.url()));
    page.on("pageerror", (error) => problems.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") problems.push(message.text());
    });
    const loadPython = await holdPythonGrammar(page);
    await interceptCommands(page);
    await page.addInitScript(recordEditorStartup);
    await page.addInitScript(installHolds, [holds.live, holds.decisions]);
    await page.goto("/level2");
    // Software canvas rendering can stretch the replay's many short event timers on CI.
    await expect(page.locator("#root")).toHaveAttribute("data-seq", String(holds.live.seq - 1), {
      timeout: 60_000,
    });
    await engaged(page, holds.live, 60_000);

    const editorRequests = () => requested.filter((url) => editorAssets.has(new URL(url).pathname));
    await expectNoEditorBeforeGraph(page);
    await expect(page.locator(".monaco-editor")).toHaveCount(0);
    const font = page.locator('link[rel="preload"][as="font"]');
    await expect(font).toHaveAttribute("href", /atkinson-hyperlegible-next-latin-400.*\.woff2$/);
    await expect(font).toHaveAttribute("crossorigin", "");
    const fontUrl = new URL((await font.getAttribute("href")) ?? "", baseURL).href;
    expect(requested.filter((url) => url === fontUrl)).toHaveLength(1);
    expect(
      await page.evaluate(() => document.fonts.check('15px "Atkinson Hyperlegible Next"')),
    ).toBe(true);

    const card = page.getByRole("article", { name: "Agent A" });
    if (first === "file") await tapNode(page, "shop.pricing:compute_price");
    else if (first === "live diff") await card.getByRole("button", { name: "Live diff" }).click();
    else {
      await release(page, holds.live);
      await engaged(page, holds.decisions, 60_000);
      // Prefetch never mounts an editor or changes watch mode by itself.
      await expect(page.locator(".monaco-editor")).toHaveCount(0);
      await card.getByRole("button", { name: "Review change" }).click();
    }
    const panel = page.getByRole("complementary", {
      name:
        first === "file" ? "File view" : first === "live diff" ? "Live diff panel" : "Review panel",
    });
    await expect(panel.locator(".monaco-editor .view-lines").last()).toBeVisible();
    await loadPython();
    await expectPython(panel);
    if (first === "review") {
      await expect(panel.getByLabel("Editable review file")).toHaveAttribute("data-state", "clean");
      // Review leaves fewer visible lines; the fixture's added docstring is near the end.
      await panel.locator(":is(.native-edit-context, textarea.inputarea)").last().focus();
      await page.keyboard.press("Control+End");
    }
    if (first !== "file") {
      await expect(panel.locator(".monaco-diff-editor .line-insert").first()).toBeVisible();
      await expect
        .poll(() => requested.filter((url) => /editor\.worker-.*\.js$/.test(url)).length)
        .toBeGreaterThan(0);
    }
    expect(editorRequests().length).toBeGreaterThan(0);
    const origin = new URL(baseURL ?? "").origin;
    expect(requested.filter((url) => new URL(url).origin !== origin)).toEqual([]);
    expect(requested.filter((url) => /\/(?:ts|json|html|css)\.worker-/.test(url))).toEqual([]);
    expect(problems).toEqual([]);
  });
}

test("the graph waits for the preloaded Next Latin 400 font", async ({ page }) => {
  const editorRequests: string[] = [];
  page.on("request", (request) => {
    if (editorAssets.has(new URL(request.url()).pathname)) editorRequests.push(request.url());
  });
  let releaseFont!: () => void;
  const waiting = new Promise<void>((resolve) => {
    releaseFont = resolve;
  });
  await page.route(/atkinson-hyperlegible-next-latin-400.*\.woff2$/, async (route) => {
    await waiting;
    await route.continue();
  });
  await page.addInitScript(installHolds, [holds.live]);
  try {
    await page.goto("/level2", { waitUntil: "domcontentloaded" });
    await engaged(page, holds.live, 60_000);
    const graph = page.getByLabel("Repository graph", { exact: true });
    expect(await graph.evaluate((host: GraphProbeHost) => host.oversightGraph)).toBeUndefined();
    expect(editorRequests).toEqual([]);
    releaseFont();
    await expect
      .poll(() =>
        graph.evaluate((host: GraphProbeHost) => host.oversightGraph?.nodeCenters().length ?? 0),
      )
      .toBeGreaterThan(0);
  } finally {
    releaseFont();
    await page.unrouteAll({ behavior: "wait" });
  }
});
