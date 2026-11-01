import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Browser, Page } from "@playwright/test";
import { expect as check } from "@playwright/test";
import { afterAll, beforeAll, expect, it } from "vitest";
import { createServer, type ViteDevServer } from "vite";
import { launchBrowser } from "../browserLaunch.support";
// eslint-disable-next-line no-restricted-imports -- Offline optional graph fixture only.
import { readBoltonsSnapshot } from "../graph/exactFixtures.testSupport";
import type { GraphProbeHost } from "../graph";
import type {} from "./mapLines.browser.fixture";

let server: ViteDevServer, browser: Browser, url: string, html: string;
const boltons = readBoltonsSnapshot();
const shots = fileURLToPath(new URL("../../../.shots/map-lines/", import.meta.url));
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}cache`,
    server: { host: "127.0.0.1", port: 0 },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { mountEvidence, reviewEvidence, demoEvidence, evidenceTaps } from "/src/app/mapLines.browser.fixture.tsx"; window.mapLinesTest = { mount: mountEvidence, review: reviewEvidence, demo: demoEvidence, taps: evidenceTaps };</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser({ webGL: true, softwareRendering: true });
  if (process.env.MAP_LINES_SHOTS === "1") await mkdir(shots, { recursive: true });
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
          ?.oversightGraph?.animating(),
      ),
    )
    .toBe(false);
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
}
async function setup(width = 1920, theme = "dark", real = false) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1280 ? 800 : 1080 },
    reducedMotion: "reduce",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.mapLinesTest);
  await page.evaluate(
    ({ graph, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.mapLinesTest.mount(graph ?? undefined, graph ? "boltons" : "map fixture");
    },
    { graph: real ? boltons : null, theme },
  );
  await ready(page);
  return page;
}
async function camera(page: Page) {
  return page.evaluate(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]'),
      p = host?.oversightGraph;
    return {
      zoom: p?.zoom(),
      pan: p?.pan(),
      level: p?.level(),
      focus: document.activeElement?.getAttribute("aria-label"),
    };
  });
}
async function zoomTo(page: Page, target: number) {
  for (let i = 0; i < 8; i++) {
    const zoom = (await camera(page)).zoom ?? 1;
    if (Math.abs(zoom - target) < 0.00001) break;
    await page.getByRole("img", { name: "Repository graph" }).dispatchEvent("wheel", {
      deltaY: -250 * Math.log10(target / zoom),
      clientX: 400,
      clientY: 400,
      bubbles: true,
    });
    await page.waitForTimeout(40);
  }
  await ready(page);
  expect((await camera(page)).zoom).toBeCloseTo(target, 4);
}
async function modulePoint(page: Page, id: string) {
  const box = await page.locator(`[data-map-module="${id}"] [data-map-circle]`).boundingBox();
  if (!box) throw new Error(`Missing circle ${id}`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}
async function groupPoint(page: Page, id: string) {
  const box = await page.locator(`[data-map-group="${id}"] .map-header`).boundingBox();
  if (!box) throw new Error(`Missing group ${id}`);
  return { x: box.x + 10, y: box.y + 10 };
}
async function pointAt(page: Page, point: { x: number; y: number }) {
  await page.mouse.move(point.x, point.y);
  await page.waitForTimeout(40);
}
async function clearPointer(page: Page) {
  await page.mouse.move(5, 5);
  await page.waitForTimeout(40);
}
it("shows calls for module/group hover alone, without graph render, camera or focus changes", async () => {
  const page = await setup();
  try {
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    await clearPointer(page);
    await check(
      page.locator(".map-layer svg, [data-map-call-chip], [data-map-distance]"),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "Zoom out", exact: true }).focus();
    await page.evaluate(() => {
      document
        .querySelector('[aria-label="Repository graph"]')
        ?.addEventListener("graphrender", () => {
          document.body.dataset.renders = String(Number(document.body.dataset.renders ?? "0") + 1);
        });
      document.body.dataset.renders = "0";
    });
    const before = await camera(page);
    await pointAt(page, await modulePoint(page, "library.module_00"));
    await check(page.locator("[data-map-line]")).toHaveCount(4);
    await check(page.locator("[data-map-line] polygon")).toHaveCount(4);
    expect(await camera(page)).toEqual(before);
    expect(await page.locator("body").getAttribute("data-renders")).toBe("0");
    await pointAt(page, await groupPoint(page, "library"));
    await check(page.locator("[data-map-line]")).toHaveCount(4);
    expect(await camera(page)).toEqual(before);
    await clearPointer(page);
    await check(
      page.locator(".map-layer svg, [data-map-call-chip], [data-map-distance]"),
    ).toHaveCount(0);
  } finally {
    await page.close();
  }
});
it.each([1280, 1920])(
  "centers a group on cards within the zoom clamp at %i without blur/taps",
  async (width) => {
    const page = await setup(width);
    try {
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await ready(page);
      await page.getByRole("button", { name: "Zoom out", exact: true }).focus();
      const point = await groupPoint(page, "library");
      await page.mouse.click(point.x, point.y);
      await ready(page);
      const c = await camera(page);
      expect(c.level).toBe("cards");
      expect(c.zoom).toBeGreaterThanOrEqual(0.6875);
      expect(c.zoom).toBeLessThanOrEqual(1);
      expect(c.focus).toBe("Zoom out");
      const centers = await page.evaluate(() => {
        const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]'),
          p = host?.oversightGraph;
        const bounds = p?.nodeBounds(".module").filter((n) => n.id.startsWith("library.")),
          clear = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
        if (!host || !p || !bounds?.length || !clear) throw new Error("Missing graph geometry");
        const h = host.getBoundingClientRect(),
          pan = p.pan(),
          zoom = p.zoom();
        return {
          actual: {
            x:
              ((Math.min(...bounds.map((n) => n.x1)) + Math.max(...bounds.map((n) => n.x2))) *
                zoom) /
                2 +
              pan.x,
            y:
              ((Math.min(...bounds.map((n) => n.y1)) + Math.max(...bounds.map((n) => n.y2))) *
                zoom) /
                2 +
              pan.y,
          },
          expected: {
            x: (40 + Math.min(h.width - 40, clear.right - h.left)) / 2,
            y:
              (Math.max(40, clear.top - h.top) + Math.min(h.height - 40, clear.bottom - h.top)) / 2,
          },
        };
      });
      expect(centers.actual.x).toBeCloseTo(centers.expected.x, 0);
      expect(centers.actual.y).toBeCloseTo(centers.expected.y, 0);
      expect(await page.evaluate(() => window.mapLinesTest.taps())).toBe(0);
    } finally {
      await page.close();
    }
  },
);
it("opens a folded module at 100% and leaves an open module open on the next visit", async () => {
  const page = await setup();
  try {
    await page.getByRole("button", { name: "Modules", exact: true }).click();
    await ready(page);
    for (let i = 0; i < 2; i++) {
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await ready(page);
      const point = await modulePoint(page, "tools.module_12");
      await page.getByRole("button", { name: "Zoom out", exact: true }).focus();
      await page.mouse.click(point.x, point.y);
      await ready(page);
      expect(await camera(page)).toMatchObject({ level: "cards", zoom: 1, focus: "Zoom out" });
      expect(
        await page.evaluate(() =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.renderedBox("tools.module_12:run"),
        ),
      ).not.toBeNull();
    }
    expect(await page.evaluate(() => window.mapLinesTest.taps())).toBe(0);
  } finally {
    await page.close();
  }
});
it("projects review after zooming out: badges, reversed calls, entry counts, stationary hidden names", async () => {
  const page = await setup();
  try {
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    await zoomTo(page, 0.3);
    const before = await modulePoint(page, "tools.module_15");
    await page.evaluate(() => {
      window.mapLinesTest.review();
    });
    await ready(page);
    await zoomTo(page, 0.3);
    const positions = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>("[data-map-module]")].map((m) => ({
        id: m.dataset.mapModule,
        y: m.querySelector("[data-map-circle]")?.getBoundingClientRect().y,
        name: getComputedStyle(m.querySelector(".map-name") ?? m).visibility,
      })),
    );
    expect(positions.find((m) => m.id === "tools.module_15")?.name).toBe("hidden");
    await check(page.locator("[data-map-distance]")).toHaveCount(3);
    await check(page.locator('[data-map-line="cone"]')).toHaveCount(4);
    await check(page.locator(".map-header").filter({ hasText: "3 of 3 in tools" })).toHaveCount(1);
    const point = await modulePoint(page, "tools.module_15");
    await pointAt(page, point);
    await check(page.locator('[data-map-module="tools.module_15"] .map-name')).toHaveCSS(
      "visibility",
      "visible",
    );
    expect(await modulePoint(page, "tools.module_15")).toEqual(point);
    expect(before.x).toBeTypeOf("number");
    await clearPointer(page);
    await check(page.locator('[data-map-module="tools.module_15"] .map-name')).toHaveCSS(
      "visibility",
      "hidden",
    );
  } finally {
    await page.close();
  }
});
it("keeps the demo on cards without map lines, badges or click behavior", async () => {
  const page = await setup(1280);
  try {
    await page.evaluate(() => {
      window.mapLinesTest.demo();
    });
    await ready(page);
    await zoomTo(page, 0.2);
    expect((await camera(page)).level).toBe("cards");
    await check(page.locator(".map-layer")).toHaveCSS("opacity", "0");
    await check(
      page.locator(".map-layer svg, [data-map-call-chip], [data-map-distance]"),
    ).toHaveCount(0);
  } finally {
    await page.close();
  }
});
for (const width of [1920, 1280])
  for (const theme of ["dark", "day"]) {
    it(`captures evidence at ${String(width)} in ${theme}`, async () => {
      const page = await setup(width, theme, true);
      const capture = async (name: string) => {
        if (process.env.MAP_LINES_SHOTS === "1")
          await page.screenshot({ path: `${shots}${name}-${String(width)}-${theme}.png` });
      };
      try {
        await page.getByRole("button", { name: "Fit the graph to the view" }).click();
        await ready(page);
        await clearPointer(page);
        expect((await camera(page)).level).toBe("map");
        await capture("map");
        const id = boltons ? "boltons.typeutils" : "library.module_00",
          group = boltons ? "boltons" : "library";
        await pointAt(page, await modulePoint(page, id));
        await capture("map-hover");
        await check(page.locator("[data-map-line]").first()).toBeVisible();
        const measured = await page.evaluate(() => {
          const size = (selector: string, property: "width" | "height") => {
            const el = document.querySelector(selector);
            return el ? el.getBoundingClientRect()[property] : null;
          };
          return {
            ring: size(".map-ring", "width"),
            chip: size(".map-decision", "height"),
            line: getComputedStyle(document.querySelector("[data-map-line] line") ?? document.body)
              .strokeWidth,
            tested: [...document.querySelectorAll(".map-header")].map((e) => e.textContent),
          };
        });
        expect(measured.ring).toBe(20);
        expect(measured.chip).toBe(22);
        expect(measured.line).toBe("1.4px");
        await pointAt(page, await groupPoint(page, group));
        await capture("map-hover-group");
        const click = await groupPoint(page, group);
        await page.mouse.click(click.x, click.y);
        await ready(page);
        const groupZoom = (await camera(page)).zoom;
        await capture("group");
        await page.evaluate(() => {
          window.mapLinesTest.review();
        });
        await ready(page);
        await zoomTo(page, width === 1280 ? 0.2 : 0.3);
        await clearPointer(page);
        await capture("map-cone");
        const badge = await page.locator("[data-map-distance]").first().boundingBox();
        expect(badge?.width).toBe(20);
        if (process.env.MAP_LINES_SHOTS === "1")
          await writeFile(
            `${shots}measurements-${String(width)}-${theme}.json`,
            JSON.stringify(
              {
                ...measured,
                badge: badge?.width,
                snapshot: boltons ? "boltons" : "synthetic",
                groupZoom,
              },
              null,
              2,
            ),
          );
      } finally {
        await page.close();
      }
    }, 60_000);
  }
