import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Locator, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type { GraphProbeHost } from "../graph";
import type {} from "./shell.browser.fixture";

const out = fileURLToPath(new URL("../../../.shots/life-edges/", import.meta.url));
let server: ViteDevServer, browser: Browser, html: string, url: string;
declare global {
  interface Window {
    lifeMasks: () => unknown;
    mountChips: () => Promise<void>;
  }
}
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${out}cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    `<html><body><script type="module">import "/src/theme/index.css"; import { shellTest } from "/src/app/shell.browser.fixture.tsx"; window.shellTest = shellTest; import { overlayBoxes } from "/src/graph/field/mask.ts"; window.lifeMasks = () => overlayBoxes(document.querySelector('[aria-label="Graph stage"] canvas')); window.mountChips = async () => { const { nodeChipTest } = await import("/src/app/nodeChip.browser.fixture.tsx"); nodeChipTest.mount(21); };</script></body></html>`,
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser({ webGL: true, softwareRendering: true });
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function withPage(width: number, theme: string, test: (page: Page) => Promise<void>) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : 800 },
    reducedMotion: "reduce",
  });
  page.setDefaultTimeout(10_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.shellTest);
    await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
      window.shellTest.mount();
    }, theme);
    await page.locator("[data-graph-clear-area]").waitFor();
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => {
      const graph = document.querySelector<GraphProbeHost>(
        '[aria-label="Repository graph"]',
      )?.oversightGraph;
      return (graph?.nodeCenters().length ?? 0) > 0 && !graph?.animating();
    });
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              resolve();
            }),
          ),
        ),
    );
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}

async function lifeEnabled(page: Page, enabled: boolean) {
  await page.evaluate((enabled) => {
    const style = document.querySelector<HTMLStyleElement>(
      'style[data-vite-dev-id$="/shell/life.css"]',
    );
    if (!style?.sheet) throw new Error("Missing life stylesheet");
    style.sheet.disabled = !enabled;
  }, enabled);
}

async function lightTop(page: Page, selector: string) {
  const session = await page.context().newCDPSession(page);
  try {
    const { root } = await session.send("DOM.getDocument");
    const { nodeId } = await session.send("DOM.querySelector", { nodeId: root.nodeId, selector });
    const { node } = await session.send("DOM.describeNode", { nodeId });
    const light = node.pseudoElements?.find((pseudo) => pseudo.pseudoType === "before");
    if (!light) throw new Error(`Missing light for ${selector}`);
    const { model } = await session.send("DOM.getBoxModel", {
      backendNodeId: light.backendNodeId,
    });
    return model.border[1];
  } finally {
    await session.detach();
  }
}

async function edges(page: Page, theme: string) {
  for (const selector of [
    ".shell-sidebar",
    ".focus-panel",
    ".bottom-dock",
    ".agents-expanded",
    ".agents-rail",
  ]) {
    const root = page.locator(selector);
    if ((await root.count()) === 0) continue;
    const seen = await root.evaluate((element) => {
      const style = getComputedStyle(element, "::before");
      const reference = document.createElement("span");
      reference.style.background =
        "linear-gradient(90deg, transparent, var(--edge-light) 30%, var(--edge-light) 70%, transparent)";
      element.append(reference);
      const background = getComputedStyle(reference).backgroundImage;
      reference.remove();
      return {
        absentInDay: element.matches(".focus-panel, .bottom-dock"),
        dock: element.matches(".bottom-dock"),
        top: element.getBoundingClientRect().top,
        height: style.height,
        width: style.width,
        inner: element.clientWidth,
        content: style.content,
        background: style.backgroundImage,
        expected: background,
        pointer: style.pointerEvents,
        z: style.zIndex,
      };
    });
    if (theme === "day" && seen.absentInDay) expect(seen.content).toBe("none");
    else {
      expect(seen).toMatchObject({ height: "1px", pointer: "none", z: "3", content: '""' });
      expect(Number.parseFloat(seen.width)).toBe(seen.inner);
      expect(seen.background).toBe(seen.expected);
      // The dock's existing 1px top border precedes its in-flow light.
      expect(await lightTop(page, selector)).toBe(seen.top + (seen.dock ? 1 : 0));
    }
  }
}

async function masks(page: Page) {
  return page.evaluate(() => window.lifeMasks());
}

async function activeTransformSelector(page: Page) {
  const selectors = await page.evaluate(() => {
    const style = document.querySelector<HTMLStyleElement>(
      'style[data-vite-dev-id$="/shell/life.css"]',
    );
    if (!style?.sheet) throw new Error("Missing life stylesheet");
    return Array.from(style.sheet.cssRules)
      .filter(
        (rule): rule is CSSStyleRule =>
          rule instanceof CSSStyleRule &&
          rule.selectorText.includes(":active") &&
          rule.style.transform !== "",
      )
      .map((rule) => rule.selectorText);
  });
  expect(selectors).toHaveLength(1);
  const selector = selectors[0];
  if (!selector) throw new Error("Missing active transform rule");
  return selector;
}

async function keyboardFocus(page: Page, target: Locator) {
  // Real Tab navigation establishes :focus-visible even after a pointer interaction.
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => element === document.activeElement)) return;
  }
  throw new Error("Keyboard focus did not reach the target");
}

async function glow(page: Page, target: Locator) {
  await keyboardFocus(page, target);
  const look = () =>
    target.evaluate((element) => {
      const style = getComputedStyle(element);
      const reference = document.createElement("span");
      reference.style.filter = "drop-shadow(0 0 6px var(--focus-glow))";
      element.append(reference);
      const expected = getComputedStyle(reference).filter;
      reference.remove();
      return {
        filter: style.filter,
        expected,
        outline: [style.outlineWidth, style.outlineStyle, style.outlineColor, style.outlineOffset],
      };
    });
  const built = await look();
  await lifeEnabled(page, false);
  const base = await look();
  await lifeEnabled(page, true);
  expect(built.filter).toBe(built.expected);
  expect(built.filter).not.toBe("none");
  expect(built.outline).toEqual(base.outline);
  expect(built.outline).toMatchObject({ 0: "2px", 1: "solid", 3: "2px" });
}

async function capture(page: Page, name: string, width: number, theme: string, target?: Locator) {
  if (process.env.LIFE_EDGES_SHOTS !== "1") return;
  await mkdir(out, { recursive: true });
  const box = await target?.boundingBox();
  const dy = await target?.evaluate((e) => new DOMMatrix(getComputedStyle(e).transform).m42);
  await page.screenshot({
    path: `${out}${name}-${String(width)}-${theme}.png`,
    ...(box
      ? {
          clip: {
            x: box.x - 18,
            y: Math.max(0, box.y - (dy ?? 0) - 10),
            width: box.width + 36,
            height: box.height + 22,
          },
        }
      : {}),
  });
}

it.each([
  [1920, "dark"],
  [1920, "day"],
  [1280, "dark"],
  [1280, "day"],
] as const)(
  "keeps panel edges and field masks at %s in %s (main 04a32ab)",
  async (width, theme) => {
    await withPage(width, theme, async (page) => {
      await capture(page, "rest-edge", width, theme);
      await page.getByRole("button", { name: "Files", exact: true }).click();
      await edges(page, theme);
      const before = await masks(page);
      const boxes = await page.locator(".shell-sidebar, .agents-expanded").evaluateAll((roots) =>
        roots.map((root) => {
          const r = root.getBoundingClientRect();
          return [r.x, r.y, r.width, r.height];
        }),
      );
      await lifeEnabled(page, false);
      expect(await masks(page)).toEqual(before);
      expect(
        await page.locator(".shell-sidebar, .agents-expanded").evaluateAll((roots) =>
          roots.map((root) => {
            const r = root.getBoundingClientRect();
            return [r.x, r.y, r.width, r.height];
          }),
        ),
      ).toEqual(boxes);
      await lifeEnabled(page, true);
      await page.evaluate(() => {
        window.shellTest.open("file");
      });
      await page.keyboard.press("Control+Backquote");
      await check(page.locator(".bottom-dock")).toBeVisible();
      await edges(page, theme);
      await check(page.locator(".agents-rail")).toHaveCount(1);
    });
  },
  60_000,
);

it.each([
  [1920, "dark"],
  [1920, "day"],
  [1280, "dark"],
  [1280, "day"],
] as const)(
  "preserves rings and presses only enabled controls at %s in %s",
  async (width, theme) => {
    await withPage(width, theme, async (page) => {
      const button = page.getByRole("button", {
        name: "Find a function, file or command",
        exact: true,
      });
      const chip = page.getByRole("button", { name: /^Decisions:/ });
      // The shell fixture omits the inbox callback; enable its real chip only to inspect focus.
      await chip.evaluate((element: HTMLButtonElement) => {
        element.disabled = false;
      });
      await glow(page, chip);
      await capture(page, "focus-glow-chip", width, theme, chip);
      await glow(page, button);
      await capture(page, "focus-glow-button", width, theme, button);
      await page.mouse.click(700, 55);
      await capture(page, "press-up", width, theme, button);
      const box = await button.boundingBox();
      if (!box) throw new Error("Missing button");
      await page.emulateMedia({ reducedMotion: "no-preference" });
      expect(
        await button.evaluate((e) => [
          getComputedStyle(e).transitionDuration,
          getComputedStyle(e).transitionTimingFunction,
        ]),
      ).toEqual(["0.07s", "ease-out"]);
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await check(button).toHaveCSS("transform", "matrix(1, 0, 0, 1, 0, 1.5)");
      expect((await button.boundingBox())?.y).toBe(box.y + 1.5);
      await capture(page, "press-down", width, theme, button);
      await page.mouse.up();
      // The palette is a sibling dialog, so transforming its trigger cannot anchor it.
      const palette = page.getByRole("dialog", { name: "Find a function, file or command" });
      await check(palette).toBeVisible();
      expect(
        await palette.evaluate((e) => {
          for (let p = e.parentElement; p; p = p.parentElement)
            if (getComputedStyle(p).transform !== "none") return false;
          return true;
        }),
      ).toBe(true);
      await page.keyboard.press("Escape");
      await page.emulateMedia({ reducedMotion: "reduce" });
      expect(await button.evaluate((e) => getComputedStyle(e).transitionDuration)).toBe("0s");
      await page.mouse.move(box.x + 5, box.y + 5);
      await page.mouse.down();
      expect(await button.evaluate((e) => getComputedStyle(e).transform)).toBe(
        "matrix(1, 0, 0, 1, 0, 1.5)",
      );
      await page.mouse.move(700, 55);
      await page.mouse.up();
      // Inspect the transform rule independently of Chromium's transient :active state.
      const pressSelector = (await activeTransformSelector(page)).replaceAll(":active", "");
      expect(await button.evaluate((e, selector) => e.matches(selector), pressSelector)).toBe(true);
      for (const aria of [false, true]) {
        await chip.evaluate((e: HTMLButtonElement, aria) => {
          e.disabled = !aria;
          e.setAttribute("aria-disabled", String(aria));
        }, aria);
        const bounds = await chip.boundingBox();
        if (!bounds) throw new Error("Missing chip");
        await page.mouse.move(bounds.x + 5, bounds.y + 5);
        await page.mouse.down();
        expect(await chip.evaluate((e, selector) => e.matches(selector), pressSelector)).toBe(
          false,
        );
        expect(await chip.evaluate((e) => getComputedStyle(e).transform)).toBe("none");
        expect(await chip.boundingBox()).toEqual(bounds);
        await page.mouse.up();
      }
    });
  },
  60_000,
);

it("keeps a chip popover still while its button presses, excluding editor and graph controls", async () => {
  await withPage(1920, "dark", async (page) => {
    await page.evaluate(() => {
      window.shellTest.open("file");
    });
    const editor = page
      .locator(".monaco-editor :is(textarea.inputarea, .native-edit-context)")
      .first();
    await editor.focus();
    expect(await editor.evaluate((e) => getComputedStyle(e).filter)).toBe("none");
    const graph = page.getByRole("img", { name: "Repository graph" });
    await graph.evaluate((e) => {
      e.tabIndex = 0;
      e.focus();
    });
    expect(await graph.evaluate((e) => getComputedStyle(e).filter)).toBe("none");
    await page.evaluate(() => window.mountChips());
    await page.waitForFunction(
      () =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.animating() === false,
    );
    await page.getByRole("button", { name: "Functions", exact: true }).click();
    await page.waitForFunction(
      () =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.animating() === false,
    );
    const chip = page.getByRole("button", {
      name: /^(9 of 10 differ|varied on 1 of 2 inputs), compute_price$/,
    });
    await chip.click();
    const popover = page.locator("[data-chip-popover]");
    const before = await popover.boundingBox();
    const box = await chip.boundingBox();
    if (!box || !before) throw new Error("Missing chip or popover");
    await page.mouse.move(box.x + 5, box.y + 5);
    await page.mouse.down();
    expect((await chip.boundingBox())?.y).toBe(box.y + 1.5);
    expect(await popover.boundingBox()).toEqual(before);
    await page.mouse.move(700, 55);
    await page.mouse.up();
  });
}, 60_000);
