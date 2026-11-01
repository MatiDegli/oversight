import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Browser, Page } from "@playwright/test";
import { expect as check } from "@playwright/test";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createServer, type ViteDevServer } from "vite";
import { launchBrowser } from "../browserLaunch.support";
// eslint-disable-next-line no-restricted-imports -- Read-only optional repository fixture, as required by this front.
import { readBoltonsSnapshot } from "../graph/exactFixtures.testSupport";
import type { GraphProbeHost } from "../graph";
import type {} from "./map.browser.fixture";

let server: ViteDevServer, browser: Browser, url: string, html: string;
const boltons = readBoltonsSnapshot();
const shots = fileURLToPath(new URL("../../../.shots/map/", import.meta.url));
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { mountMap, mountMapSize, mountMapDemo, mapTick, mapSelect } from "/src/app/map.browser.fixture.tsx"; window.mapTest = { mount: mountMap, sized: mountMapSize, demo: mountMapDemo, tick: mapTick, select: mapSelect };</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser({ webGL: true, softwareRendering: true });
  await mkdir(shots, { recursive: true });
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function ready(page: Page) {
  await check
    .poll(() =>
      page.evaluate(() =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.positions(),
      ),
    )
    .not.toBeUndefined();
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          resolve();
        }),
      ),
    );
  });
  await check
    .poll(() =>
      page.evaluate(() =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.animating(),
      ),
    )
    .toBe(false);
}
async function withMap(
  action: (page: Page) => Promise<void>,
  graph = boltons,
  width = 1920,
  height = 1080,
  theme = "dark",
  baseline = false,
) {
  const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    if (baseline)
      await page.route("**/src/app/shell/GraphStage.tsx*", async (route) => {
        const response = await route.fetch();
        const body = await response.text();
        expect(body).toContain("map: true");
        await route.fulfill({ response, body: body.replace("map: true", "map: false") });
      });
    await page.goto(url);
    await page.waitForFunction(() => !!window.mapTest);
    await page.evaluate(
      ({ graph, theme }) => {
        document.documentElement.dataset.theme = theme;
        window.mapTest.mount(graph ?? undefined, graph ? "boltons" : "map fixture");
      },
      { graph, theme },
    );
    await ready(page);
    await action(page);
    expect(errors).toEqual([]);
  } catch (error) {
    throw new Error(`${String(error)}; browser errors: ${errors.join("; ")}`, { cause: error });
  } finally {
    await page.close();
  }
}
async function level(page: Page) {
  return page.evaluate(() =>
    document
      .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
      ?.oversightGraph?.level(),
  );
}
async function zoomTo(page: Page, target: number, settle = 300) {
  // Real wheel navigation rather than a writable probe.
  const host = page.getByRole("img", { name: "Repository graph" });
  for (let i = 0; i < 8; i += 1) {
    const current = await page.evaluate(
      () =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.zoom() ?? 1,
    );
    if (Math.abs(current - target) < 0.000001) break;
    await host.dispatchEvent("wheel", {
      deltaY:
        -250 *
        Math.log10(
          target /
            (await page.evaluate(
              () =>
                document
                  .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                  ?.oversightGraph?.zoom() ?? 1,
            )),
        ),
      clientX: 400,
      clientY: 400,
      bubbles: true,
    });
    await page.waitForTimeout(40);
  }
  if (settle) await page.waitForTimeout(settle);
  const actual = await page.evaluate(() =>
    document
      .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
      ?.oversightGraph?.zoom(),
  );
  expect(actual, `wheel target ${String(target)}`).toBeCloseTo(target, 4);
}
async function assertPanels(page: Page) {
  const result = await page.evaluate(() => {
    const area = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
    const panels = [...document.querySelectorAll<HTMLElement>("[data-map-group]")].map((el) => {
      const b = el.getBoundingClientRect();
      return { x1: b.left, y1: b.top, x2: b.right, y2: b.bottom };
    });
    return {
      area: area && { x1: area.left, y1: area.top, x2: area.right, y2: area.bottom },
      panels,
      circles: [...document.querySelectorAll("[data-map-circle]")].map(
        (el) => el.getBoundingClientRect().width,
      ),
      opacity: getComputedStyle(
        document.querySelector('[aria-label="Repository graph"]') ?? document.body,
      ).opacity,
      chips: [...document.querySelectorAll<HTMLElement>("[data-chip-node]")].filter(
        (el) => getComputedStyle(el).visibility === "visible",
      ).length,
    };
  });
  expect(result.panels.length).toBeGreaterThanOrEqual(2);
  expect(new Set(result.circles).size).toBe(1);
  expect(result.opacity).toBe("0");
  expect(result.chips).toBe(0);
  const area = result.area;
  if (!area) throw new Error("Missing clear area");
  for (const box of result.panels) {
    expect(box.x1).toBeGreaterThanOrEqual(area.x1 - 1);
    expect(box.y1).toBeGreaterThanOrEqual(area.y1 - 1);
    expect(box.x2).toBeLessThanOrEqual(area.x2 + 1);
    expect(box.y2).toBeLessThanOrEqual(area.y2 + 1);
  }
  for (let i = 0; i < result.panels.length; i += 1)
    for (const b of result.panels.slice(i + 1)) {
      const a = result.panels[i];
      if (!a) continue;
      expect(Math.max(b.x1 - a.x2, a.x1 - b.x2, b.y1 - a.y2, a.y1 - b.y2)).toBeGreaterThanOrEqual(
        48,
      );
    }
}

it("uses hysteresis in both directions and preserves focus and camera on events", async () => {
  await withMap(async (page) => {
    await page.getByRole("button", { name: "Zoom out", exact: true }).focus();
    await zoomTo(page, 0.646);
    expect(await level(page)).toBe("map");
    await zoomTo(page, 0.6875);
    expect(await level(page)).toBe("map");
    const before = await page.evaluate(() => ({
      camera: document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.pan(),
      focus: document.activeElement?.getAttribute("aria-label"),
    }));
    await page.evaluate(() => {
      window.mapTest.tick();
    });
    expect(await level(page)).toBe("map");
    expect(
      await page.evaluate(() => ({
        camera: document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.pan(),
        focus: document.activeElement?.getAttribute("aria-label"),
      })),
    ).toEqual(before);
    await zoomTo(page, 0.69);
    expect(await level(page)).toBe("cards");
    await zoomTo(page, 0.648);
    expect(await level(page)).toBe("cards");
  }, null);
});
it.each(["synthetic", ...(boltons ? ["boltons"] : [])])(
  "keeps selection current on %s cards after a map visit without a store event",
  async (kind) => {
    await withMap(
      async (page) => {
        const ids = await page.evaluate(() =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.nodeCenters(".module")
            .map((node) => node.id),
        );
        if (!ids || ids.length < 2) throw new Error("Missing modules");
        const selected = () =>
          page.evaluate(() =>
            document
              .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph?.nodeCenters(":selected")
              .map((node) => node.id),
          );
        for (const id of [ids[0], ids[1], null]) {
          await zoomTo(page, 0.3);
          expect(await level(page)).toBe("map");
          await page.evaluate((id) => {
            window.mapTest.select(id ?? null);
          }, id);
          await zoomTo(page, 0.7);
          expect(await level(page)).toBe("cards");
          expect(await selected()).toEqual(id === null ? [] : [id]);
        }
      },
      kind === "boltons" ? boltons : null,
    );
  },
);
it("preserves the routing omissions notice after visiting the map", async () => {
  await withMap(async (page) => {
    await zoomTo(page, 0.7);
    await page.evaluate(() => {
      document
        .querySelector('[aria-label="Repository graph"]')
        ?.dispatchEvent(new CustomEvent("routingomissions", { detail: 7 }));
    });
    const notice = page
      .getByRole("status")
      .filter({ hasText: "+7 call routes omitted: no clear lane" });
    await check(notice).toBeVisible();
    await zoomTo(page, 0.3);
    expect(await level(page)).toBe("map");
    await check(notice).toHaveCount(0);
    await zoomTo(page, 0.7);
    expect(await level(page)).toBe("cards");
    await check(notice).toBeVisible();
  }, null);
});
it("keeps the world point under the pointer fixed during map wheel zoom", async () => {
  await withMap(async (page) => {
    await zoomTo(page, 0.3);
    const area = await page.locator("[data-graph-clear-area]").boundingBox();
    if (!area) throw new Error("Missing clear area");
    const pointer = {
      x: Math.round(area.x + area.width * 0.3),
      y: Math.round(area.y + area.height * 0.4),
    };
    const atPointer = () =>
      page.evaluate((pointer) => {
        const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
        const probe = host?.oversightGraph;
        if (!host || !probe) throw new Error("Missing graph");
        const box = host.getBoundingClientRect(),
          pan = probe.pan(),
          zoom = probe.zoom();
        return {
          x: (pointer.x - box.left - pan.x) / zoom,
          y: (pointer.y - box.top - pan.y) / zoom,
          zoom,
        };
      }, pointer);
    const before = await atPointer();
    await page.mouse.move(pointer.x, pointer.y);
    await page.mouse.wheel(0, -20);
    await check.poll(async () => (await atPointer()).zoom).toBeGreaterThan(before.zoom);
    const after = await atPointer();
    expect(after.x).toBeCloseTo(before.x, 4);
    expect(after.y).toBeCloseTo(before.y, 4);
    expect(await level(page)).toBe("map");
  }, null);
});
it("drag-pans the map over a module without moving nodes or stealing focus", async () => {
  await withMap(async (page) => {
    await page.getByRole("button", { name: "Modules", exact: true }).click();
    await ready(page);
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    expect(await level(page)).toBe("map");
    const focus = page.getByRole("button", { name: "Zoom out", exact: true });
    const snapshot = () =>
      page.evaluate(() => {
        const probe = document.querySelector<GraphProbeHost>(
          '[aria-label="Repository graph"]',
        )?.oversightGraph;
        return { pan: probe?.pan(), positions: probe?.positions(), draggable: probe?.draggable() };
      });
    const before = await snapshot();
    expect(before.draggable).toEqual([]);
    const circle = await page.locator("[data-map-circle]").first().boundingBox();
    if (!circle) throw new Error("Missing module circle");
    await page.mouse.move(circle.x + circle.width / 2, circle.y + circle.height / 2);
    await page.mouse.down();
    // Cytoscape blurs the current control on mouse-down in cards and map alike. Navigation
    // after that native gesture must not move keyboard focus again.
    await focus.focus();
    await page.mouse.move(circle.x + circle.width / 2 + 80, circle.y + circle.height / 2 + 40, {
      steps: 8,
    });
    await page.mouse.up();
    const after = await snapshot();
    expect((after.pan?.x ?? 0) - (before.pan?.x ?? 0)).toBeCloseTo(80, 0);
    expect((after.pan?.y ?? 0) - (before.pan?.y ?? 0)).toBeCloseTo(40, 0);
    expect(after.positions).toEqual(before.positions);
    await check(focus).toBeFocused();
    await zoomTo(page, 0.7);
    expect((await snapshot()).draggable?.length).toBeGreaterThan(0);
  }, null);
});
it.each([23, 24])("gates zoom and Fit on %i shown module nodes", async (modules) => {
  await withMap(async (page) => {
    await page.evaluate((count) => {
      window.mapTest.sized(count);
    }, modules);
    await ready(page);
    expect(
      await page.evaluate(
        () =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.nodeBounds(".module").length,
      ),
    ).toBe(modules);
    for (const zoom of [0.646, 0.6875, 0.69, 0.2]) {
      await zoomTo(page, zoom);
      expect(await level(page)).toBe(modules === 24 && zoom <= 0.6875 ? "map" : "cards");
    }
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    expect(await level(page)).toBe(modules === 24 ? "map" : "cards");
    if (modules === 23) await check(page.getByLabel("Zoom percentage")).not.toContainText("Map");
    else await check(page.getByLabel("Zoom percentage")).toContainText("Map");
  }, null);
});
it.each(
  ["synthetic", ...(boltons ? ["boltons"] : [])].flatMap((kind) =>
    [1280, 1920].map((width) => ({ kind, width })),
  ),
)("Fit frames $kind panels at $width in the clear area", async ({ kind, width }) => {
  await withMap(
    async (page) => {
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await ready(page);
      expect(await level(page)).toBe("map");
      await assertPanels(page);
      await page.getByRole("button", { name: "Modules", exact: true }).click();
      await ready(page);
      expect(await level(page)).toBe("map");
      await assertPanels(page);
    },
    kind === "boltons" ? boltons : null,
    width,
    width === 1280 ? 800 : 1080,
  );
});
it.each([1280, 1920])("keeps the demo on cards after Fit and zoom at %i", async (width) => {
  await withMap(
    async (page) => {
      await page.evaluate(() => {
        window.mapTest.demo();
      });
      await ready(page);
      await page.getByRole("button", { name: "Modules", exact: true }).click();
      await ready(page);
      const before = await page.evaluate(() =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.positions(),
      );
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await ready(page);
      const info = await page.evaluate(() => ({
        zoom: document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.zoom(),
        width: document.querySelector('[aria-label="Repository graph"]')?.clientWidth,
        clear: (() => {
          const b = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
          return b && { x: b.x, y: b.y, width: b.width, height: b.height };
        })(),
      }));
      if (process.env.MAP_SHOTS === "1")
        await writeFile(`${shots}demo-fit-${String(width)}.json`, JSON.stringify(info, null, 2));
      expect(await level(page), JSON.stringify(info)).toBe("cards");
      expect(
        await page.evaluate(() =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.positions(),
        ),
      ).toEqual(before);
      await zoomTo(page, 0.2);
      expect(await level(page)).toBe("cards");
      await check(page.getByLabel("Zoom percentage")).not.toContainText("Map");
    },
    null,
    width,
    width === 1280 ? 800 : 1080,
  );
});

it("records the base demo Fit and unchanged positions without the map opt-in", async () => {
  await withMap(
    async (page) => {
      await page.evaluate(() => {
        window.mapTest.demo();
      });
      await ready(page);
      await page.getByRole("button", { name: "Modules", exact: true }).click();
      await ready(page);
      const before = await page.evaluate(() =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.positions(),
      );
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await ready(page);
      const result = await page.evaluate(() => {
        const p = document.querySelector<GraphProbeHost>(
          '[aria-label="Repository graph"]',
        )?.oversightGraph;
        return { zoom: p?.zoom(), level: p?.level(), positions: p?.positions() };
      });
      expect(result.level).toBe("cards");
      expect(result.zoom).toBeLessThan(11 / 17);
      expect(result.positions).toEqual(before);
      if (process.env.MAP_SHOTS === "1")
        await writeFile(
          `${shots}demo-base-fit-1280.json`,
          JSON.stringify({ zoom: result.zoom, level: result.level }, null, 2),
        );
    },
    null,
    1280,
    800,
    "dark",
    true,
  );
});

async function captureCrossfade(page: Page, suffix: string) {
  await zoomTo(page, 0.7);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  // Pause the real CSS transitions at equal opacity, preserving the product easing.
  await zoomTo(page, 0.646, 0);
  await page.evaluate(() => {
    for (const el of [
      document.querySelector(".map-layer"),
      document.querySelector('[aria-label="Repository graph"]'),
    ]) {
      for (const animation of el?.getAnimations() ?? []) {
        animation.pause();
        const duration = Number(animation.effect?.getTiming().duration ?? 240);
        let low = 0,
          high = duration;
        for (let i = 0; i < 18; i += 1) {
          const mid = (low + high) / 2;
          animation.currentTime = mid;
          const progress = animation.effect?.getComputedTiming().progress ?? 0;
          if (progress < 0.5) low = mid;
          else high = mid;
        }
        animation.currentTime = (low + high) / 2;
      }
    }
  });
  const opacity = await page.evaluate(() =>
    [
      document.querySelector(".map-layer"),
      document.querySelector('[aria-label="Repository graph"]'),
    ].map((el) => Number(getComputedStyle(el ?? document.body).opacity)),
  );
  expect(opacity[0]).toBeCloseTo(0.5, 2);
  expect(opacity[1]).toBeCloseTo(0.5, 2);
  if (process.env.MAP_SHOTS === "1")
    await page.screenshot({ path: `${shots}cross${suffix}`, animations: "allow" });
}

describe.skipIf(!boltons)("approved map states and optional captures", () => {
  for (const [width, height] of [
    [1920, 1080],
    [1280, 800],
  ] as const)
    for (const theme of ["dark", "day"]) {
      it(`captures map, Fit, crossfade and demo at ${String(width)} in ${theme}`, async () => {
        await withMap(
          async (page) => {
            const suffix = `${width === 1280 ? "-1280" : ""}-${theme}.png`;
            await page.getByRole("button", { name: "Fit the graph to the view" }).click();
            await ready(page);
            await zoomTo(page, width === 1280 ? 0.2 : 0.3);
            expect(await level(page)).toBe("map");
            await check(page.getByLabel("Zoom percentage")).toContainText("Map");
            await check(page.getByRole("img", { name: "Repository graph" })).toHaveCSS(
              "opacity",
              "0",
            );
            await check(page.locator(".map-layer")).toContainText("Agent A · 1 changed");
            await check(page.locator(".map-layer")).toContainText("Agent B · editing");
            if (process.env.MAP_SHOTS === "1")
              await page.screenshot({ path: `${shots}map${suffix}` });
            await page.getByRole("button", { name: "Modules", exact: true }).click();
            await ready(page);
            await page.getByRole("button", { name: "Fit the graph to the view" }).click();
            await ready(page);
            await assertPanels(page);
            if (process.env.MAP_SHOTS === "1")
              await page.screenshot({ path: `${shots}fit-notests${suffix}` });
            const fit = await page.evaluate(() => ({
              zoom: document
                .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                ?.oversightGraph?.zoom(),
              level: document
                .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                ?.oversightGraph?.level(),
              cardFit: (() => {
                const host = document.querySelector<GraphProbeHost>(
                  '[aria-label="Repository graph"]',
                );
                const bounds = host?.oversightGraph?.nodeBounds();
                const clear = document
                  .querySelector("[data-graph-clear-area]")
                  ?.getBoundingClientRect();
                if (!host || !bounds?.length || !clear) return null;
                const h = host.getBoundingClientRect();
                return Math.min(
                  (Math.min(h.width - 40, clear.right - h.left) - 40) /
                    (Math.max(...bounds.map((b) => b.x2)) - Math.min(...bounds.map((b) => b.x1))),
                  (Math.min(h.height - 40, clear.bottom - h.top) -
                    Math.max(40, clear.top - h.top)) /
                    (Math.max(...bounds.map((b) => b.y2)) - Math.min(...bounds.map((b) => b.y1))),
                );
              })(),
            }));
            if (process.env.MAP_SHOTS === "1")
              await writeFile(
                `${shots}fit-${String(width)}-${theme}.json`,
                JSON.stringify(fit, null, 2),
              );
            await captureCrossfade(page, suffix);
            await page.emulateMedia({ reducedMotion: "reduce" });
            await page.evaluate(() => {
              window.mapTest.demo();
            });
            await ready(page);
            await page.getByRole("button", { name: "Fit the graph to the view" }).click();
            await ready(page);
            await page.getByRole("button", { name: "Modules", exact: true }).click();
            await ready(page);
            if (process.env.MAP_SHOTS === "1")
              await page.screenshot({ path: `${shots}demo${suffix}` });
            expect(await level(page)).toBe("cards");
            await check(page.getByLabel("Zoom percentage")).not.toContainText("Map");
          },
          boltons,
          width,
          height,
          theme,
        );
      }, 60_000);
    }
});
