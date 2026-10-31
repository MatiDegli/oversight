import { launchBrowser } from "../browserLaunch.support";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type {} from "./shell.browser.fixture";
import type { GraphProbeHost } from "../graph";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let html: string;
let url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/shell-cache", import.meta.url)),
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html><body><script type="module">import "/src/theme/index.css"; import { shellTest } from "/src/app/shell.browser.fixture.tsx"; window.shellTest = shellTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});
async function withPage(width: number, height: number, test: (page: Page) => Promise<void>) {
  if (!browser) throw new Error("Browser not started");
  const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.shellTest);
    await page.evaluate(() => {
      window.shellTest.mount();
    });
    await page.locator("[data-graph-clear-area]").waitFor();
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}
async function box(page: Page, selector: string) {
  const value = await page.locator(selector).boundingBox();
  if (!value) throw new Error(`Missing ${selector}`);
  return value;
}

async function framed(page: Page, id: string) {
  await check
    .poll(() =>
      page.evaluate((id) => {
        const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
        const graph = host?.oversightGraph;
        const node = graph?.nodeCenters().find((node) => node.id === graph.representative(id));
        return {
          framed:
            !!host &&
            !!node &&
            !graph?.animating() &&
            node.x > 0 &&
            node.x < host.clientWidth &&
            node.y > 0 &&
            node.y < host.clientHeight,
          node,
          size: host ? [host.clientWidth, host.clientHeight] : null,
          ids: graph?.nodeCenters().map((node) => node.id),
        };
      }, id),
    )
    .toMatchObject({ framed: true });
}

/** Read rendered module frames and overlay boxes in the same viewport coordinates. */
async function watchGeometry(page: Page) {
  await check
    .poll(() =>
      page
        .locator('[aria-label="Repository graph"]')
        .evaluate((host: GraphProbeHost) => (host.oversightGraph?.nodeCenters().length ?? 0) > 0),
    )
    .toBe(true);
  return page.evaluate(() => {
    const boundsOf = (element: Element) => {
      const b = element.getBoundingClientRect();
      return {
        left: b.left,
        right: b.right,
        top: b.top,
        bottom: b.bottom,
        width: b.width,
        height: b.height,
      };
    };
    const element = (selector: string) => {
      const found = document.querySelector(selector);
      if (!found) throw new Error(`Missing ${selector}`);
      return found;
    };
    const rect = (selector: string) => boundsOf(element(selector));
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const graph = host?.oversightGraph;
    const detail = element('[aria-label="Graph detail"]').parentElement;
    if (!host || !graph || !detail) throw new Error("Missing graph or detail controls");
    const pan = graph.pan();
    const zoom = graph.zoom();
    const bounds = host.getBoundingClientRect();
    return {
      zoom,
      graph: boundsOf(host),
      panel: rect(".agents-expanded"),
      detail: boundsOf(detail),
      controls: rect('[aria-label="Zoom"]'),
      clear: rect("[data-graph-clear-area]"),
      modules: graph.nodeBounds(".module").map((b) => ({
        x1: bounds.left + b.x1 * zoom + pan.x,
        x2: bounds.left + b.x2 * zoom + pan.x,
        y1: bounds.top + b.y1 * zoom + pan.y,
        y2: bounds.top + b.y2 * zoom + pan.y,
      })),
    };
  });
}

it("keeps watch controls and fitted module frames clear of floating agents at 1280x800", async () => {
  await withPage(1280, 800, async (page) => {
    const start = await watchGeometry(page);
    expect(start.detail.right).toBeLessThanOrEqual(start.panel.left - 24);
    expect(start.controls.right).toBeLessThanOrEqual(start.panel.left - 24);
    expect(start.clear.right).toBe(start.panel.left - 24);
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    const fit = await watchGeometry(page);
    expect(fit.modules).toHaveLength(6);
    for (const frame of fit.modules) {
      expect(frame.x1).toBeGreaterThanOrEqual(fit.graph.left);
      expect(frame.x2).toBeLessThanOrEqual(fit.panel.left - 24 + 0.01);
    }
  });
});

it.each([false, true])("keeps focus overlays clear at 1280x800 (expanded=%s)", async (expanded) => {
  await withPage(1280, 800, async (page) => {
    await page.evaluate(() => {
      window.shellTest.open("file");
    });
    if (expanded)
      await page.getByRole("button", { name: "Start an agent here", exact: true }).click();
    await check(page.getByLabel("Graph detail", { exact: true })).toHaveCount(0);
    const graph = await box(page, '[aria-label="Repository graph"]');
    const panel = await box(page, '[aria-label="Agents"]');
    const zoom = await box(page, '[aria-label="Zoom"]');
    const clear = await box(page, "[data-graph-clear-area]");
    const legend = await box(page, '[aria-label="Legend"]');
    const right = (b: { x: number; width: number }) => b.x + b.width;
    expect(panel.width).toBe(expanded ? 400 : 56);
    if (expanded) {
      for (const b of [zoom, clear, legend])
        expect(right(b)).toBeLessThanOrEqual(panel.x - 24 + 0.5);
    } else {
      for (const b of [zoom, clear])
        expect(Math.abs(right(b) - (right(graph) - 24))).toBeLessThanOrEqual(0.5);
      expect(right(legend)).toBeLessThanOrEqual(zoom.x);
    }
  });
});

it("starts with all six demo modules at Fit zoom and preserves docked overlay boxes at 1920x1080", async () => {
  await withPage(1920, 1080, async (page) => {
    const start = await watchGeometry(page);
    expect(start.zoom).toBeLessThan(1);
    await check(page.getByLabel("Zoom percentage")).toHaveText(
      `${String(Math.round(start.zoom * 100))}%`,
    );
    expect(start.detail.right).toBe(start.graph.right - 24);
    expect(start.controls.right).toBe(start.graph.right - 24);
    expect(start.modules).toHaveLength(6);
    for (const frame of start.modules) {
      expect(frame.x1).toBeGreaterThanOrEqual(start.clear.left);
      expect(frame.x2).toBeLessThanOrEqual(start.clear.right);
      expect(frame.y1).toBeGreaterThanOrEqual(start.clear.top);
      expect(frame.y2).toBeLessThanOrEqual(start.clear.bottom);
    }
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    const fit = await watchGeometry(page);
    expect(Math.abs(start.zoom - fit.zoom)).toBeLessThanOrEqual(0.01);
    expect(fit.detail).toEqual(start.detail);
    expect(fit.controls).toEqual(start.controls);
  });
});

async function openDockWithActivity(page: Page) {
  await page.keyboard.press("Control+Backquote");
  expect((await box(page, ".bottom-dock")).height).toBe(280);
  const dock = await box(page, ".bottom-dock");
  const center = await box(page, ".shell-center");
  expect(dock.x).toBe(center.x);
  expect(dock.width).toBe(center.width);
  await framed(page, "shop.pricing:compute_price");
  const tab = page.getByRole("tab", { name: "Activity log", exact: true });
  await tab.click();
  const activity = page.getByRole("tabpanel", { name: "Activity log" });
  await check(
    activity.getByRole("list", { name: "Run activity" }).getByRole("listitem"),
  ).toHaveText([
    "Asearch · okSearched for compute_price(: 9 matches in 4 files0.04 s11:03:16 ART",
    "Aread_file · okRead compute_price in pricing.py, lines 14 to 220.01 s11:03:18 ART",
    "Aedit_file · okEdited compute_price in pricing.py: +2 -00.03 s11:03:21 ART",
  ]);
  await check(tab).toHaveAttribute("aria-selected", "true");
  await check(tab).toBeFocused();
}

it.each([
  [1920, 1080],
  [1440, 900],
])(
  "measures every zone at %sx%s and preserves the graph across toggles and tabs",
  async (width, height) => {
    await withPage(width, height, async (page) => {
      expect((await box(page, ".shell-frame > header")).height).toBe(52);
      const chip = page.getByRole("button", { name: /^Decisions: 0 pending/ });
      await check(chip).toHaveText("Nothing needs you");
      const chipBefore = await chip.boundingBox();
      expect((await box(page, ".left-rail")).width).toBe(56);
      expect((await box(page, '[aria-label="Status line"]')).height).toBe(24);
      expect((await box(page, '[aria-label="Agents"]')).width).toBe(400);
      expect(
        await page.locator(".agents-expanded").evaluate((node) => getComputedStyle(node).position),
      ).toBe(width === 1440 ? "absolute" : "static");
      await page.evaluate(() => {
        const node = document.querySelector('[aria-label="Repository graph"]');
        Object.assign(window, { originalGraph: node });
      });
      await page.getByRole("button", { name: "Files", exact: true }).click();
      expect((await box(page, ".shell-sidebar")).width).toBe(280);
      expect(
        await page.locator(".shell-sidebar").evaluate((node) => getComputedStyle(node).position),
      ).toBe(width === 1440 ? "absolute" : "static");
      await page.evaluate(() => {
        window.shellTest.open("diff");
      });
      expect((await box(page, ".focus-panel")).width).toBe(width === 1920 ? 880 : 768);
      expect((await box(page, '[aria-label="Agents"]')).width).toBe(56);
      expect((await box(page, ".graph-stage")).width).toBeGreaterThanOrEqual(560);
      await page.keyboard.press("Control+b");
      await check(page.locator(".shell-sidebar")).toHaveCount(0);
      await openDockWithActivity(page);
      await page.evaluate(() => {
        window.shellTest.open("file");
        window.shellTest.update();
        window.shellTest.open("review");
      });
      const tabs = page.getByRole("tablist", { name: "Open files and changes" }).getByRole("tab");
      await check(tabs).toHaveCount(3);
      await check(tabs.last()).toContainText("ready for review");
      await check(page.getByRole("button", { name: /^Decisions: 1 pending/ })).toHaveText(
        "Decisions · 1",
      );
      const chipAfter = await page.getByRole("button", { name: /^Decisions:/ }).boundingBox();
      expect(chipAfter?.height).toBe(chipBefore?.height);
      expect(chipAfter?.y).toBe(chipBefore?.y);
      await tabs.first().click();
      await framed(page, "shop.pricing:compute_price");
      await check(tabs.first()).toHaveAttribute("aria-selected", "true");
      expect(
        await page.evaluate(
          () =>
            document.querySelector('[aria-label="Repository graph"]') ===
            Reflect.get(window, "originalGraph"),
        ),
      ).toBe(true);
      await page.getByRole("button", { name: "Maximize focus panel", exact: true }).click();
      expect((await box(page, ".focus-panel")).width).toBe(
        (await box(page, ".shell-center")).width,
      );
      await check(page.locator(".graph-stage")).toBeHidden();
      await page.getByRole("button", { name: "Show graph", exact: true }).click();
      await framed(page, "shop.pricing:compute_price");
      expect((await box(page, ".graph-stage")).width).toBeGreaterThanOrEqual(560);
      await page.keyboard.press("Alt+w");
      await check(tabs).toHaveCount(2);
      await page.keyboard.press("Escape");
      await check(tabs).toHaveCount(1);
      await page.keyboard.press("Escape");
      await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
      await page.keyboard.press("Control+b");
      await check(page.locator(".shell-sidebar")).toHaveCount(1);
      await page.keyboard.press("Control+b");
      await check(page.locator(".shell-sidebar")).toHaveCount(0);
      await page.getByRole("button", { name: "Keyboard shortcuts", exact: true }).click();
      await check(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
      await page.keyboard.press("Escape");
      await check(page.getByRole("dialog")).toHaveCount(0);
    });
  },
  60_000,
);

it("ignores shell shortcuts in the task box and Monaco, and events never open a tab", async () => {
  await withPage(1920, 1080, async (page) => {
    const task = page.getByLabel("Task for the new agent");
    await task.fill("Keep typing");
    await task.focus();
    for (const key of ["Control+b", "Control+Backquote", "Alt+w", "Escape"])
      await page.keyboard.press(key);
    await check(task).toBeFocused();
    await check(task).toHaveValue("Keep typing");
    await check(page.locator(".shell-sidebar, .bottom-dock")).toHaveCount(0);
    await page.evaluate(() => {
      window.shellTest.update();
    });
    await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
    await page.evaluate(() => {
      window.shellTest.open("file");
    });
    const editor = page
      .locator(".monaco-editor :is(textarea.inputarea, .native-edit-context)")
      .first();
    await editor.focus();
    for (const key of ["Control+b", "Control+Backquote", "Alt+w", "Escape"])
      await page.keyboard.press(key);
    await check(page.locator(".focus-panel")).toHaveCount(1);
    await check(page.locator(".shell-sidebar, .bottom-dock")).toHaveCount(0);
    await check(editor).toBeFocused();
    await page.getByRole("button", { name: "Close file", exact: true }).focus();
    await page.keyboard.press("Alt+w");
    await check(page.locator(".focus-panel")).toHaveCount(0);
    await page.setViewportSize({ width: 1200, height: 900 });
    await check(page.getByText("This window is too narrow.", { exact: false })).toBeVisible();
  });
}, 40_000);

it.each(["Meta+Control+b", "Meta+Control+Backquote", "Meta+Alt+w", "Meta+Escape"])(
  "ignores the Meta-modified shell shortcut %s",
  async (key) => {
    await withPage(1920, 1080, async (page) => {
      await page.evaluate(() => {
        window.shellTest.open("file");
      });
      await page.getByRole("button", { name: "Close file", exact: true }).focus();
      await page.keyboard.press(key);
      await check(page.locator(".shell-sidebar, .bottom-dock")).toHaveCount(0);
      await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(1);
      await page.keyboard.press("Alt+w");
      await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
    });
  },
  30_000,
);

it("Watch closes only the active tab and retains the other tabs", async () => {
  await withPage(1920, 1080, async (page) => {
    await page.evaluate(() => {
      window.shellTest.open("file");
      window.shellTest.open("diff");
      window.shellTest.update();
      window.shellTest.open("review");
    });
    const tabs = page.getByRole("tablist", { name: "Open files and changes" }).getByRole("tab");
    await check(tabs).toHaveCount(3);
    await page.getByRole("button", { name: "Watch", exact: true }).click();
    await check(tabs).toHaveCount(2);
    await check(tabs.last()).toHaveAttribute("aria-selected", "true");
    await page.getByRole("button", { name: "Watch", exact: true }).click();
    await check(tabs).toHaveCount(1);
    await check(tabs.first()).toHaveAttribute("aria-selected", "true");
    await page.getByRole("button", { name: "Watch", exact: true }).click();
    await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
    await check(page.getByRole("button", { name: "Watch", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
}, 30_000);

it.each(["Control+b", "Control+Backquote", "Alt+w"])(
  "pauses %s while the shortcuts sheet is open, then resumes it",
  async (key) => {
    await withPage(1920, 1080, async (page) => {
      await page.evaluate(() => {
        window.shellTest.open("file");
      });
      await page.getByRole("button", { name: "Keyboard shortcuts", exact: true }).click();
      const sheet = page.getByRole("dialog", { name: "Keyboard shortcuts" });
      await page.keyboard.press(key);
      await check(sheet).toBeVisible();
      await check(page.locator(".shell-sidebar, .bottom-dock")).toHaveCount(0);
      await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(1);
      await page.keyboard.press("Escape");
      await check(sheet).toHaveCount(0);
      await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(1);
      await page.getByRole("button", { name: "Close file", exact: true }).focus();
      await page.keyboard.press(key);
      if (key === "Alt+w")
        await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
      else await check(page.locator(".shell-sidebar, .bottom-dock")).toHaveCount(1);
    });
  },
  30_000,
);

it("ignores Meta+Escape in the shortcuts sheet", async () => {
  await withPage(1920, 1080, async (page) => {
    await page.getByRole("button", { name: "Keyboard shortcuts", exact: true }).click();
    const sheet = page.getByRole("dialog", { name: "Keyboard shortcuts" });
    await page.keyboard.press("Meta+Escape");
    await check(sheet).toBeVisible();
    await page.keyboard.press("Escape");
    await check(sheet).toHaveCount(0);
  });
}, 30_000);

it("limits fixed blur to graph controls and fills other overlays (M8)", async () => {
  await withPage(1920, 1080, async (page) => {
    const filled = async (selector: string) => {
      const look = await page.locator(selector).evaluate((element) => {
        const reference = document.createElement("span");
        reference.style.background = element.classList.contains("graph-text-shield")
          ? "var(--panel)"
          : "var(--overlay-fill)";
        reference.style.boxShadow = "var(--top-highlight)";
        document.body.append(reference);
        const style = getComputedStyle(element);
        const expected = getComputedStyle(reference);
        const result = {
          fill: style.backgroundColor,
          expectedFill: expected.backgroundColor,
          shadow: style.boxShadow,
          expectedShadow: expected.boxShadow,
          blur: style.backdropFilter,
          inset: Math.min(
            ...[style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft].map(
              Number.parseFloat,
            ),
          ),
        };
        reference.remove();
        return result;
      });
      expect(look.blur).toBe("none");
      expect(look.fill).toBe(look.expectedFill);
      expect(look.fill).not.toBe("rgba(0, 0, 0, 0)");
      expect(look.shadow).toContain(look.expectedShadow);
      expect(look.expectedShadow).not.toBe("none");
      expect(look.inset).toBeGreaterThan(0);
    };
    await filled(".graph-stage h2:has-text('demo-repo') >> ..");
    await page.evaluate(() => {
      window.shellTest.update();
      window.shellTest.open("review");
    });
    await filled('[aria-label="Possibly affected"]');
    const blurred = await page.evaluate(() =>
      [...document.querySelectorAll("*")]
        .filter((element) => getComputedStyle(element).backdropFilter !== "none")
        .map((element) => ({
          label: element.getAttribute("aria-label"),
          radius: getComputedStyle(element).backdropFilter,
          transition: getComputedStyle(element).transitionProperty,
          duration: getComputedStyle(element).transitionDuration,
        })),
    );
    expect(blurred).toHaveLength(2);
    for (const element of blurred) {
      expect(["Legend", "Zoom"]).toContain(element.label);
      expect(element.radius).toBe("blur(12px)");
      if (element.duration.split(",").some((duration) => Number.parseFloat(duration) > 0))
        expect(element.transition).not.toMatch(/all|filter|overlay-blur/);
    }
    expect(
      await page.evaluate(() =>
        document
          .getAnimations()
          .flatMap((animation) =>
            animation.effect instanceof KeyframeEffect
              ? animation.effect.getKeyframes().flatMap(Object.keys)
              : [],
          ),
      ),
    ).not.toEqual(expect.arrayContaining([expect.stringMatching(/filter|overlay-blur/i)]));
    await page.setViewportSize({ width: 1200, height: 900 });
    await filled(".narrow-notice");
    await page.getByRole("button", { name: "Keyboard shortcuts", exact: true }).click();
    await filled(".shortcuts-sheet");
  });
}, 40_000);
