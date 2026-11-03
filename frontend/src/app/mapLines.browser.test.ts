import { openOversight } from "../workspaceView.browser.support";
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
// eslint-disable-next-line no-restricted-imports -- Read-only lamp evidence from the existing probe.
import type { FieldHost } from "../graph/field/controller";
import { themes } from "../theme";
// eslint-disable-next-line no-restricted-imports -- Independent CPU oracle for the unchanged card profile.
import { createLampField, fieldConfig, parseHex } from "../graph/field/model";
import type {} from "./mapLines.browser.fixture";

let server: ViteDevServer, browser: Browser, url: string, html: string;
const boltons = readBoltonsSnapshot();
const sweep = process.env.MAP_SWEEP_FINDING;
const phase = process.env.MAP_SWEEP_PHASE ?? "after";
const checkpoint = process.env.MAP_SWEEP_CHECKPOINT ?? "working tree";
const sweepRoot =
  process.env.MAP_SWEEP_OUTPUT ??
  fileURLToPath(new URL("../../../.shots/nit-sweep-b11/", import.meta.url));
const frameErrors = new WeakMap<Page, { pageErrors: string[]; consoleErrors: string[] }>();
const shots = fileURLToPath(new URL("../../../.shots/map-lines/", import.meta.url));
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}cache`,
    server: { host: "127.0.0.1", port: 0 },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { mountEvidence, reviewEvidence, demoEvidence, evidenceTaps, stationaryEvidence } from "/src/app/mapLines.browser.fixture.tsx"; window.mapLinesTest = { mount: mountEvidence, review: reviewEvidence, demo: demoEvidence, taps: evidenceTaps, stationary: stationaryEvidence };</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser({
    webGL: true,
    softwareRendering: true,
    args: ["--disable-features=LocalNetworkAccessChecks"],
  });
  if (process.env.MAP_LINES_SHOTS === "1") await mkdir(shots, { recursive: true });
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function ready(page: Page) {
  await openOversight(page);
  await check
    .poll(
      () =>
        page.evaluate(() =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.animating(),
        ),
      // Software rendering can delay even an idle probe after review Fit.
      { timeout: 15_000 },
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
  const errors = { pageErrors: [] as string[], consoleErrors: [] as string[] };
  frameErrors.set(page, errors);
  page.on("pageerror", (error) => errors.pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.consoleErrors.push(message.text());
  });
  await page.route("**/?field-time=2", (route) =>
    route.fulfill({ contentType: "text/html", body: html }),
  );
  await page.goto(`${url}?field-time=2`);
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
  const area = await page.locator("[data-graph-clear-area]").boundingBox();
  if (!area) throw new Error("Missing zoom area");
  for (let i = 0; i < 8; i++) {
    const zoom = (await camera(page)).zoom ?? 1;
    if (Math.abs(zoom - target) < 0.00001) break;
    await page.getByRole("img", { name: "Repository graph" }).dispatchEvent("wheel", {
      deltaY: -250 * Math.log10(target / zoom),
      clientX: area.x + area.width / 2,
      clientY: area.y + area.height / 2,
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
async function unobstructedNames(page: Page) {
  const overlaps = await page.evaluate(() => {
    const labels = [...document.querySelectorAll(".map-name, .map-count")];
    return [...document.querySelectorAll(".map-agent, [data-field-markers] g")].flatMap((agent) => {
      if (getComputedStyle(agent).visibility === "hidden") return [];
      const a = agent.getBoundingClientRect();
      return labels.flatMap((label) => {
        const b = label.getBoundingClientRect();
        return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
          ? [
              {
                agent: agent.textContent,
                label: label.textContent,
                a: { left: a.left, right: a.right, top: a.top, bottom: a.bottom },
                b: { left: b.left, right: b.right, top: b.top, bottom: b.bottom },
              },
            ]
          : [];
      });
    });
  });
  expect(overlaps).toEqual([]);
}
async function fullAgentLabel(page: Page, id: string) {
  const agent = page.locator(`[data-map-module="${id}"] .map-agent`);
  await check(page.locator(`[data-map-module="${id}"] .map-name`)).toHaveClass(/map-full-name/);
  await check(agent).toHaveText("B editing");
  await check(agent).toHaveCSS("max-width", "none");
  await check(agent).toHaveCSS("overflow", "visible");
  await check(agent).toHaveCSS("text-overflow", "clip");
  expect(await agent.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  expect(await agent.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(
    await page
      .locator(`[data-map-module="${id}"] .map-name`)
      .evaluate((el) => getComputedStyle(el).backgroundColor),
  );
}
it.each(["dark", "day"])("reveals the full pointed agent label at 1280 in %s", async (theme) => {
  const page = await setup(1280, theme);
  try {
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    await zoomTo(page, 0.21);
    await clearPointer(page);
    const id = "library.module_01";
    const agent = page.locator(`[data-map-module="${id}"] .map-agent`);
    await check(agent).toHaveText("B editing");
    await unobstructedNames(page);
    const label = await agent.boundingBox();
    if (!label) throw new Error("Missing agent label");
    await pointAt(page, { x: label.x + label.width / 2, y: label.y + label.height / 2 });
    await fullAgentLabel(page, id);
    await unobstructedNames(page);
    await pointAt(page, await groupPoint(page, "library"));
    await fullAgentLabel(page, id);
    await unobstructedNames(page);
    await clearPointer(page);
    await check(agent).toHaveCSS("max-width", "100%");
    await check(agent).toHaveCSS("text-overflow", "ellipsis");
    await unobstructedNames(page);
  } finally {
    await page.close();
  }
});
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
it("returns from module cards to an unhovered map when Fit is clicked elsewhere", async () => {
  const page = await setup();
  try {
    const fit = page.getByRole("button", { name: "Fit the graph to the view" });
    await fit.click();
    await ready(page);
    const point = await modulePoint(page, "library.module_00");
    await pointAt(page, point);
    await check(page.locator("[data-map-line]")).toHaveCount(4);
    await page.mouse.click(point.x, point.y);
    await ready(page);
    expect((await camera(page)).level).toBe("cards");
    await fit.click();
    await ready(page);
    expect((await camera(page)).level).toBe("map");
    await check(page.locator(".map-layer svg, .map-full-name")).toHaveCount(0);
    await check(page.locator('[data-map-group="library"]')).not.toHaveAttribute(
      "style",
      /border-color/,
    );
  } finally {
    await page.close();
  }
});
it("continues a map drag outside the stage and stops on window mouseup", async () => {
  const page = await setup();
  try {
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    await page.getByRole("button", { name: "Zoom out", exact: true }).focus();
    const point = await modulePoint(page, "library.module_00");
    await pointAt(page, point);
    await page.mouse.down();
    await page.mouse.move(point.x + 10, point.y + 10);
    await clearPointer(page);
    await check(page.locator("[data-map-line]")).toHaveCount(0);
    const before = await camera(page);
    await page.mouse.move(20, 15);
    const after = await camera(page);
    expect(after.pan).toEqual({ x: (before.pan?.x ?? 0) + 15, y: (before.pan?.y ?? 0) + 10 });
    expect(after).toMatchObject({ level: "map", focus: "Zoom out", zoom: before.zoom });
    await page.mouse.up();
    await page.mouse.move(30, 25);
    expect(await camera(page)).toEqual(after);
    expect(await page.evaluate(() => window.mapLinesTest.taps())).toBe(0);
  } finally {
    await page.close();
  }
});
it("rechecks hover after viewport changes with a stationary pointer", async () => {
  const page = await setup();
  try {
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    const id = "library.module_00";
    const point = await modulePoint(page, id);
    await pointAt(page, point);
    const name = page.locator(`[data-map-module="${id}"] .map-name`);
    await check(name).toHaveClass(/map-full-name/);
    await zoomTo(page, 0.2);
    const moved = await modulePoint(page, id);
    expect(Math.hypot(moved.x - point.x, moved.y - point.y)).toBeGreaterThan(40);
    const expected = await page.evaluate((point) => {
      const contains = (el: Element) => {
        const box = el.getBoundingClientRect();
        return (
          point.x >= box.left && point.x <= box.right && point.y >= box.top && point.y <= box.bottom
        );
      };
      const modules = [...document.querySelectorAll<HTMLElement>("[data-map-module]")];
      const module = modules.find(contains);
      if (module) return [module.dataset.mapModule];
      const group = [...document.querySelectorAll<HTMLElement>("[data-map-group]")].find(contains);
      return group
        ? [...(group.parentElement?.querySelectorAll<HTMLElement>("[data-map-module]") ?? [])].map(
            (m) => m.dataset.mapModule,
          )
        : [];
    }, point);
    await check
      .poll(() =>
        page
          .locator("[data-map-module]")
          .evaluateAll((modules) =>
            modules
              .filter((m) => m.querySelector(".map-full-name"))
              .map((m) => m.getAttribute("data-map-module")),
          ),
      )
      .toEqual(expected);
    expect((await camera(page)).level).toBe("map");
    await clearPointer(page);
    await check(page.locator("[data-map-line]")).toHaveCount(0);
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
      const centers = await page.evaluate(() => {
        const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]'),
          probe = host?.oversightGraph;
        const bounds = probe?.nodeBounds(".module").find((node) => node.id === "tools.module_12"),
          clear = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
        if (!host || !probe || !bounds || !clear) throw new Error("Missing module geometry");
        const box = host.getBoundingClientRect(),
          pan = probe.pan(),
          zoom = probe.zoom();
        return {
          actual: {
            x: ((bounds.x1 + bounds.x2) * zoom) / 2 + pan.x,
            y: ((bounds.y1 + bounds.y2) * zoom) / 2 + pan.y,
          },
          expected: {
            x: (40 + Math.min(box.width - 40, clear.right - box.left)) / 2,
            y:
              (Math.max(40, clear.top - box.top) +
                Math.min(box.height - 40, clear.bottom - box.top)) /
              2,
          },
        };
      });
      expect(centers.actual.x).toBeCloseTo(centers.expected.x, 0);
      expect(centers.actual.y).toBeCloseTo(centers.expected.y, 0);
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
    await clearPointer(page);
    await check(page.locator('[data-map-module="tools.module_15"] .map-name')).toHaveCSS(
      "visibility",
      "hidden",
    );
  } finally {
    await page.close();
  }
});
it("keeps the circle at its pre-cone position when the dimmed name hides", async () => {
  const page = await setup();
  try {
    await page.evaluate(() => {
      window.mapLinesTest.stationary();
    });
    const name = page.locator('[data-map-module="tools.module_15"] .map-name');
    await check(name).toHaveCSS("visibility", "visible");
    const before = await modulePoint(page, "tools.module_15");
    await page.evaluate(() => {
      window.mapLinesTest.stationary(true);
    });
    await check(name).toHaveCSS("visibility", "hidden");
    expect(await modulePoint(page, "tools.module_15")).toEqual(before);
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
it("fits header rows at wrapping boundaries using the rendered borders and type weights", async () => {
  const page = await setup();
  try {
    for (const group of ["library", "tools"]) {
      if (group === "tools") {
        await page.evaluate(() => {
          window.mapLinesTest.review();
        });
        await ready(page);
      }
      const zoom = (await camera(page)).zoom ?? 1;
      const target = await page.locator(`[data-map-group="${group}"]`).evaluate((panel, zoom) => {
        const header = panel.querySelector(".map-header");
        if (!header) throw new Error("Missing header");
        const items = [...header.children];
        const content = items.reduce((sum, item) => sum + item.getBoundingClientRect().width, 0);
        const worldWidth = (panel.getBoundingClientRect().width - 12) / zoom;
        // One pixel short of a full row after padding and both panel borders.
        return (content + (items.length - 1) * 10 + 26 - 12 - 1) / worldWidth;
      }, zoom);
      await zoomTo(page, target);
      const headers = await page.locator(".map-header").evaluateAll((elements) =>
        elements.map((header) => ({
          height: header.getBoundingClientRect().height,
          rows: new Set(
            [...header.children].map((item) => {
              const box = item.getBoundingClientRect();
              return Math.round(box.y + box.height / 2);
            }),
          ).size,
        })),
      );
      const rows = Math.max(...headers.map((header) => header.rows));
      expect(rows).toBeGreaterThan(1);
      for (const header of headers) expect(header.height).toBeCloseTo(37 + 23 * (rows - 1), 1);
    }
  } finally {
    await page.close();
  }
});
/** Count B's visible lamp centers, never guide dots, labels, masks or transient rings.
 * The 84px apron is fixed in screen space. At least 150 lit centers rejects the launch
 * result (a few lamps), while allowing viewport-dependent dither and mask alignment.
 * Reduced motion holds full steady strengths and disables working/milestone rings.
 */
async function measurePool(page: Page, id: string) {
  return page.evaluate((id) => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const canvas = document.querySelector<FieldHost>(
      '[aria-label="Graph stage"] canvas[aria-hidden]',
    );
    const cell = document.querySelector(`[data-map-module="${id}"]`);
    if (!host?.oversightGraph || !canvas?.oversightField || !cell)
      throw new Error("Missing lamp evidence");
    const field = canvas.oversightField;
    const origin = canvas.getBoundingClientRect();
    const bounds = cell.getBoundingClientRect();
    const box = {
      x1: bounds.left - origin.left,
      y1: bounds.top - origin.top,
      x2: bounds.right - origin.left,
      y2: bounds.bottom - origin.top,
    };
    const region = {
      x1: Math.max(0, box.x1 - 84),
      y1: Math.max(0, box.y1 - 84),
      x2: Math.min(origin.width, box.x2 + 84),
      y2: Math.min(origin.height, box.y2 + 84),
    };
    const hidden = [
      ...document.querySelectorAll(".map-name, .map-count, .map-agent, [data-map-circle]"),
    ]
      .filter((el) => getComputedStyle(el).visibility !== "hidden")
      .map((el) => {
        const r = el.getBoundingClientRect();
        return {
          x1: r.left - origin.left,
          y1: r.top - origin.top,
          x2: r.right - origin.left,
          y2: r.bottom - origin.top,
        };
      });
    const masks = [...field.masks(), ...hidden];
    const step = Number.parseFloat(getComputedStyle(canvas).getPropertyValue("--dot-cell-size"));
    const levels = [0, 0, 0, 0];
    let excluded = 0;
    for (let y = (Math.ceil(region.y1 / step - 0.5) + 0.5) * step; y <= region.y2; y += step)
      for (let x = (Math.ceil(region.x1 / step - 0.5) + 0.5) * step; x <= region.x2; x += step) {
        if (
          x >= canvas.clientWidth ||
          y >= canvas.clientHeight ||
          masks.some((b) => x + 3.5 > b.x1 && x - 3.5 < b.x2 && y + 3.5 > b.y1 && y - 3.5 < b.y2)
        ) {
          excluded++;
          continue;
        }
        const lamp = field.lamp(x, y);
        const level =
          lamp && lamp.color[2] > lamp.color[0] && lamp.color[2] > lamp.color[1] ? lamp.level : 0;
        levels[level] = (levels[level] ?? 0) + 1;
      }
    return {
      box,
      region,
      zoom: host.oversightGraph.zoom(),
      level: host.oversightGraph.level(),
      fieldClock: 2,
      reducedMotion: true,
      step,
      excluded,
      levels,
      lit: levels.slice(1).reduce((sum, n) => sum + n, 0),
      source: "B only (strongest blue hue)",
    };
  }, id);
}
it.each(["dark", "day"])("keeps a broad working module pool at zoom 0.21 in %s", async (theme) => {
  const page = await setup(1280, theme, true);
  try {
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    await zoomTo(page, 0.21);
    await clearPointer(page);
    const pool = await measurePool(page, boltons ? "boltons.strutils" : "library.module_01");
    console.log("Working module pool", theme, pool);
    if (process.env.MAP_LINES_SHOTS === "1")
      await writeFile(`${shots}pool-21-${theme}.json`, JSON.stringify(pool, null, 2));
    expect(pool.lit).toBeGreaterThanOrEqual(150);
  } finally {
    await page.close();
  }
});
/** Measure the actual GPU pixel beneath each label over its hottest underlying map lamp. */
async function mapTextContrast(page: Page, id: string, theme: string) {
  return page.evaluate(
    ({ id, theme }) => {
      const canvas = document.querySelector<FieldHost>(
        '[aria-label="Graph stage"] canvas[aria-hidden]',
      );
      const cell = document.querySelector(`[data-map-module="${id}"]`);
      const field = canvas?.oversightField;
      if (!canvas || !cell || !field) throw new Error("Missing map contrast evidence");
      const origin = canvas.getBoundingClientRect();
      const context = document.createElement("canvas").getContext("2d");
      if (!context) throw new Error("Missing color resolver");
      const color = (css: string) => {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = css;
        context.fillRect(0, 0, 1, 1);
        return [...context.getImageData(0, 0, 1, 1).data];
      };
      const ground = color(getComputedStyle(canvas).getPropertyValue("--ground"));
      const panelElement = cell.parentElement?.querySelector(".map-panel");
      const panelStyle = getComputedStyle(panelElement ?? cell);
      const panel = panelElement ? color(panelStyle.backgroundColor) : [0, 0, 0, 0];
      panel[3] = (panel[3] ?? 0) * Number(panelStyle.opacity);
      const over = (a: number[], b: number[]) =>
        a
          .slice(0, 3)
          .map((c, i) => (c * (a[3] ?? 255)) / 255 + (b[i] ?? 0) * (1 - (a[3] ?? 255) / 255));
      const luminance = (rgb: number[]) =>
        rgb.slice(0, 3).reduce((sum, c, i) => {
          const s = c / 255;
          return (
            sum +
            (s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4) *
              ([0.2126, 0.7152, 0.0722][i] ?? 0)
          );
        }, 0);
      const contrast = (a: number[], b: number[]) =>
        (Math.max(luminance(a), luminance(b)) + 0.05) /
        (Math.min(luminance(a), luminance(b)) + 0.05);
      return [...cell.querySelectorAll(".map-name, .map-count, .map-agent")].map((el) => {
        const bounds = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        const foreground = color(style.color);
        let hottest: { x: number; y: number; color: number[]; level: number } | null = null;
        const top = (Math.ceil((bounds.top - origin.top) / 7 - 0.5) + 0.5) * 7;
        const left = (Math.ceil((bounds.left - origin.left) / 7 - 0.5) + 0.5) * 7;
        const right = bounds.right - origin.left,
          bottom = bounds.bottom - origin.top;
        for (let y = top; y < bottom; y += 7)
          for (let x = left; x < right; x += 7) {
            const lamp = field.lamp(x, y);
            if (!lamp?.level) continue;
            if (
              !hottest ||
              (theme === "dark"
                ? luminance(lamp.color) > luminance(hottest.color)
                : luminance(lamp.color) < luminance(hottest.color))
            )
              hottest = { x, y, ...lamp };
          }
        if (!hottest) throw new Error("No lit lamp beneath map text");
        const pixel = field.pixel(hottest.x, hottest.y);
        if (!pixel) throw new Error("Missing GPU pixel");
        return {
          text: el.textContent,
          lamp: hottest,
          unmasked: contrast(foreground, over(panel, hottest.color)),
          actual: contrast(
            foreground,
            over(color(style.backgroundColor), over(panel, over(pixel, ground))),
          ),
          unlitDifference: over(color(style.backgroundColor), over(panel, ground)).map((c, i) =>
            Math.abs(c - (over(panel, ground)[i] ?? 0)),
          ),
        };
      });
    },
    { id, theme },
  );
}
it.each(["dark", "day"])("keeps map text at 4.5:1 over the hottest lamp in %s", async (theme) => {
  const page = await setup(1280, theme, true);
  try {
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await ready(page);
    await zoomTo(page, 0.21);
    await clearPointer(page);
    expect((await camera(page)).level).toBe("map");
    const found = await mapTextContrast(
      page,
      boltons ? "boltons.strutils" : "library.module_01",
      theme,
    );
    console.log("Map text contrast", theme, found);
    expect(found).toHaveLength(3);
    for (const label of found) {
      expect(label.actual, label.text).toBeGreaterThanOrEqual(4.5);
      for (const difference of label.unlitDifference) expect(difference).toBeLessThanOrEqual(1);
    }
  } finally {
    await page.close();
  }
});
/** Resolve plates against the unlit panel, including its independent opacity. */
async function mapSurfaces(page: Page) {
  return page.evaluate(() => {
    const ctx = document.createElement("canvas").getContext("2d");
    if (!ctx) throw new Error("Missing color resolver");
    const color = (css: string) => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = css;
      ctx.fillRect(0, 0, 1, 1);
      return [...ctx.getImageData(0, 0, 1, 1).data];
    };
    const over = (a: number[], b: number[]) =>
      a
        .slice(0, 3)
        .map((c, i) => (c * (a[3] ?? 255)) / 255 + (b[i] ?? 0) * (1 - (a[3] ?? 255) / 255));
    return [...document.querySelectorAll("[data-map-module]")].flatMap((cell) => {
      const ground = color(getComputedStyle(cell).getPropertyValue("--ground"));
      const panel = cell.parentElement?.querySelector(".map-panel");
      const panelStyle = panel ? getComputedStyle(panel) : null;
      const panelColor = panelStyle ? color(panelStyle.backgroundColor) : [0, 0, 0, 0];
      panelColor[3] = (panelColor[3] ?? 0) * Number(panelStyle?.opacity ?? 1);
      const surface = over(panelColor, ground);
      return [...cell.querySelectorAll(".map-count, .map-agent:not(.map-agent-full)")]
        .filter((el) => getComputedStyle(el).visibility !== "hidden")
        .map((el) => {
          const style = getComputedStyle(el);
          const plate = over(color(style.backgroundColor), surface);
          const rect = el.getBoundingClientRect(),
            bounds = cell.getBoundingClientRect();
          return {
            module: cell.getAttribute("data-map-module"),
            text: el.textContent,
            hook: el.className,
            panel: Boolean(panel),
            panelOpacity: Number(panelStyle?.opacity ?? 1),
            surface,
            plate,
            unlitDifference: plate.map((c, i) => Math.abs(c - (surface[i] ?? 0))),
            width: rect.width,
            cellWidth: bounds.width,
            centerDifference: Math.abs(rect.x + rect.width / 2 - bounds.x - bounds.width / 2),
          };
        });
    });
  });
}
async function captureSurface(page: Page, width: number, theme: string) {
  await page.evaluate(() => {
    window.mapLinesTest.mount();
  });
  await clearPointer(page);
  const frame = await page.evaluate(() => window.mapLinesTest.stationary(false, true));
  await page.evaluate(() => document.fonts.ready);
  const surfaces = await mapSurfaces(page);
  const overflow = await page.evaluate(() =>
    [document.documentElement, document.body, ...document.querySelectorAll(".map-layer")].map(
      (el) => Math.max(0, el.scrollWidth - el.clientWidth),
    ),
  );
  const directory = `${sweepRoot}/${sweep ?? "M3"}/${phase}`;
  await mkdir(directory, { recursive: true });
  const stem = `${directory}/synthetic-faded-panel-${String(width)}-${theme}`;
  await page.screenshot({ path: `${stem}.png` });
  const errors = frameErrors.get(page);
  await writeFile(
    `${stem}.json`,
    JSON.stringify(
      {
        png: `${stem}.png`,
        finding: "M3",
        phase,
        checkpoint,
        uncommittedEdit: process.env.MAP_SWEEP_EDIT ?? "capture support only",
        provenance:
          "supplementary synthetic map fixture; hand-written events; all modules outside cone",
        snapshot: "map-synthetic",
        layout: "syntheticMapGraph(24) with evidenceGraph cell swaps",
        viewport: page.viewportSize(),
        theme,
        camera: frame,
        publishedLevel: frame.level,
        pointer: { x: 5, y: 5 },
        fieldClock: 2,
        reducedMotion: true,
        surfaces,
        overflow,
        ...errors,
      },
      null,
      2,
    ),
  );
  expect(frame.level).toBe("map");
  expect(errors).toEqual({ pageErrors: [], consoleErrors: [] });
  expect(overflow.every((n) => n === 0)).toBe(true);
}
it.each(["dark", "day"])(
  "matches resting plates on normal, faded and ground surfaces in %s",
  async (theme) => {
    const page = await setup(1280, theme);
    try {
      await page.evaluate(() => window.mapLinesTest.stationary());
      const normal = await mapSurfaces(page);
      expect(normal.some((label) => label.panel && label.panelOpacity === 1)).toBe(true);
      await page.evaluate(() => window.mapLinesTest.stationary(false, true));
      const faded = await mapSurfaces(page);
      expect(
        faded.some((label) => label.hook.includes("map-agent") && label.panelOpacity === 0.5),
      ).toBe(true);
      await page.evaluate(() => {
        window.mapLinesTest.demo();
      });
      // The small demo has no published interactive map; render its real no-panel model statically.
      await page.evaluate(() => window.mapLinesTest.stationary());
      const ground = await mapSurfaces(page);
      expect(ground.some((label) => !label.panel && label.hook.includes("map-agent"))).toBe(true);
      for (const label of [...normal, ...faded, ...ground]) {
        for (const difference of label.unlitDifference)
          expect(difference, label.text).toBeLessThanOrEqual(1);
        if (label.hook === "map-count") {
          expect(label.width).toBeLessThan(label.cellWidth);
          expect(label.centerDifference).toBeLessThanOrEqual(1);
        }
      }
    } finally {
      await page.close();
    }
  },
);
/** Compare every unmasked card lamp with the pre-existing card spread and mode gain. */
async function assertCardPool(page: Page, theme: "dark" | "day", real = !!boltons) {
  const sample = await page.evaluate((real) => {
    const graph = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    const canvas = document.querySelector<FieldHost>(
      '[aria-label="Graph stage"] canvas[aria-hidden]',
    );
    const field = canvas?.oversightField;
    if (!graph || !canvas || !field) throw new Error("Missing card evidence");
    const ids = real
      ? ["boltons.iterutils:is_iterable", "boltons.strutils:slugify"]
      : ["library.module_00:run", "library.module_01:run"];
    const boxes = ids.map((id) => graph.renderedBox(graph.representative(id)));
    if (boxes.some((b) => !b)) throw new Error("Missing card emitter");
    const levels: number[] = [];
    const masks = field.masks();
    for (let y = 3.5; y < Math.ceil(canvas.clientHeight / 7) * 7; y += 7)
      for (let x = 3.5; x < Math.ceil(canvas.clientWidth / 7) * 7; x += 7)
        levels.push(
          x >= canvas.clientWidth ||
            y >= canvas.clientHeight ||
            masks.some((b) => x + 3.5 > b.x1 && x - 3.5 < b.x2 && y + 3.5 > b.y1 && y - 3.5 < b.y2)
            ? -1
            : (field.lamp(x, y)?.level ?? -1),
        );
    return {
      boxes,
      levels,
      width: canvas.clientWidth,
      height: canvas.clientHeight,
      zoom: graph.zoom(),
      level: graph.level(),
    };
  }, real);
  expect(sample.level).toBe("cards");
  const base = fieldConfig(themes[theme], "watch");
  const expected = createLampField()(
    { ...base, profile: { near: 26 * sample.zoom, far: 72 * sample.zoom, gain: 0.92 } },
    sample.width,
    sample.height,
    sample.boxes.flatMap((box, i) =>
      box
        ? [
            {
              box,
              weight: i === 0 ? 0.5 : 1,
              pulses: false,
              color: parseHex(themes[theme][i === 0 ? "agent-a" : "agent-b"]),
            },
          ]
        : [],
    ),
    null,
  );
  const mismatches = sample.levels.filter(
    (level, i) => level >= 0 && level !== Math.round(((expected.data[i * 4 + 3] ?? 0) / 255) * 3),
  ).length;
  expect(mismatches).toBe(0);
  expect(sample.levels.filter((level) => level > 0).length).toBeGreaterThan(0);
  return { zoom: sample.zoom, lit: sample.levels.filter((level) => level > 0).length, mismatches };
}
async function panCardEmitterIntoView(page: Page) {
  const emitterBoxes = () =>
    page.evaluate((real) => {
      const graph = document.querySelector<GraphProbeHost>(
        '[aria-label="Repository graph"]',
      )?.oversightGraph;
      if (!graph) throw new Error("Missing card camera");
      const ids = real
        ? ["boltons.iterutils:is_iterable", "boltons.strutils:slugify"]
        : ["library.module_00:run", "library.module_01:run"];
      return {
        boxes: ids.map((id) => graph.renderedBox(graph.representative(id))),
        viewport: graph.metrics().viewport,
        zoom: graph.zoom(),
        level: graph.level(),
      };
    }, Boolean(boltons));
  const before = await emitterBoxes();
  const emitter = before.boxes[0];
  const host = await page.getByRole("img", { name: "Repository graph" }).boundingBox();
  const area = await page.locator("[data-graph-clear-area]").boundingBox();
  if (!emitter || !host || !area) throw new Error("Missing emitter framing geometry");
  // At the retained card zoom their combined height exceeds the viewport; pan one into view.
  const x = area.x + area.width / 2;
  const y = area.y + area.height - 20;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(
    x + area.x + area.width / 2 - host.x - (emitter.x1 + emitter.x2) / 2,
    y + area.y + area.height / 2 - host.y - (emitter.y1 + emitter.y2) / 2,
    { steps: 4 },
  );
  await page.mouse.up();
  await ready(page);
  return { before, after: await emitterBoxes() };
}
it.each(["dark", "day"] as const)(
  "restores card light after an explicit map move in %s",
  async (theme) => {
    const page = await setup(1280, theme, true);
    try {
      const fit = page.getByRole("button", { name: "Fit the graph to the view" });
      await fit.click();
      await ready(page);
      await zoomTo(page, 0.21);
      await clearPointer(page);
      expect(
        (await measurePool(page, boltons ? "boltons.strutils" : "library.module_01")).lit,
      ).toBeGreaterThanOrEqual(150);
      const point = await groupPoint(page, boltons ? "boltons" : "library");
      await page.mouse.click(point.x, point.y);
      await ready(page);
      const framing = await panCardEmitterIntoView(page);
      await ready(page);
      const pool = await assertCardPool(page, theme);
      console.log("Card emitter framing", theme, JSON.stringify({ ...framing, pool }));
      await fit.click();
      await ready(page);
      await zoomTo(page, 0.21);
      await clearPointer(page);
      expect(
        (await measurePool(page, boltons ? "boltons.strutils" : "library.module_01")).lit,
      ).toBeGreaterThanOrEqual(150);
    } finally {
      await page.close();
    }
  },
);
it.each(["dark", "day"] as const)(
  "keeps synthetic group-control lamps visible in %s",
  async (theme) => {
    const page = await setup(1280, theme);
    try {
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await ready(page);
      await zoomTo(page, 0.21);
      await clearPointer(page);
      const point = await groupPoint(page, "library");
      await page.mouse.click(point.x, point.y);
      await ready(page);
      await assertCardPool(page, theme, false);
    } finally {
      await page.close();
    }
  },
);
async function measureEvidence(page: Page) {
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
  return measured;
}
/** Geometry and masks from the same frame as the PNG, with serializable typed rectangles. */
async function sweepMeasurements(page: Page) {
  return page.evaluate(() => {
    const rect = (el: Element) => {
      const { x, y, width, height, top, right, bottom, left } = el.getBoundingClientRect();
      return { x, y, width, height, top, right, bottom, left };
    };
    const graph = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    const canvas = document.querySelector<FieldHost>(
      '[aria-label="Graph stage"] canvas[aria-hidden]',
    );
    const overflow = (el: Element) => ({
      surface: el.getAttribute("aria-label") ?? el.tagName,
      positive: Math.max(0, el.scrollWidth - el.clientWidth),
    });
    return {
      camera: { zoom: graph?.zoom(), pan: graph?.pan() },
      publishedLevel: graph?.level(),
      pointer: [...document.querySelectorAll(":hover")].map((el) => ({
        tag: el.tagName,
        module: el.getAttribute("data-map-module"),
      })),
      masks: canvas?.oversightField?.masks(),
      labels: [...document.querySelectorAll("[data-map-module]")].map((cell) => ({
        module: cell.getAttribute("data-map-module"),
        cell: rect(cell),
        labels: [...cell.querySelectorAll(".map-name, .map-count, .map-agent")].map((el) => ({
          text: el.textContent,
          hook: el.className,
          rect: rect(el),
          visibility: getComputedStyle(el).visibility,
          background: getComputedStyle(el).backgroundColor,
        })),
      })),
      panels: [...document.querySelectorAll(".map-panel")].map((el) => ({
        group: el.getAttribute("data-map-group"),
        opacity: getComputedStyle(el).opacity,
        rect: rect(el),
      })),
      overflow: [
        document.documentElement,
        document.body,
        ...document.querySelectorAll('[aria-label="Graph stage"], [aria-label="Repository graph"]'),
      ].map(overflow),
    };
  });
}
/** Per-frame evidence is persisted before its gates can fail. */
async function captureSweep(page: Page, name: string, width: number, theme: string) {
  if (!sweep) return;
  const directory = `${sweepRoot}/${sweep}/${phase}`;
  await mkdir(directory, { recursive: true });
  const stem = `${directory}/${name}-${String(width)}-${theme}`;
  const measurements = await sweepMeasurements(page);
  const pool =
    name === "group"
      ? null
      : await measurePool(page, boltons ? "boltons.strutils" : "library.module_01");
  const contrast =
    name === "map"
      ? await mapTextContrast(page, boltons ? "boltons.strutils" : "library.module_01", theme)
      : null;
  const errors = frameErrors.get(page);
  await page.screenshot({ path: `${stem}.png` });
  await writeFile(
    `${stem}.json`,
    JSON.stringify(
      {
        png: `${stem}.png`,
        finding: sweep,
        phase,
        checkpoint,
        uncommittedEdit: process.env.MAP_SWEEP_EDIT ?? "capture harness only",
        provenance: boltons
          ? "offline Boltons graph with hand-written map events"
          : "synthetic map fixture with hand-written events (Boltons unavailable)",
        snapshot: boltons
          ? {
              path: ".runtime/graphs/boltons-d31d821.snapshot.json",
              commit: boltons.commit,
              nodes: boltons.nodes.length,
              layout: "fixtures/layouts/boltons-d31d821.json",
            }
          : "synthetic",
        viewport: page.viewportSize(),
        theme,
        fieldClock: 2,
        reducedMotion: true,
        ...measurements,
        pool,
        contrast,
        ...errors,
      },
      null,
      2,
    ),
  );
  expect(errors).toEqual({ pageErrors: [], consoleErrors: [] });
  for (const overflow of measurements.overflow) expect(overflow.positive).toBe(0);
  if (name !== "group") expect(measurements.publishedLevel).toBe("map");
}
async function captureCone(
  page: Page,
  width: number,
  id: string,
  capture: (name: string) => Promise<void>,
) {
  await page.evaluate(() => {
    window.mapLinesTest.review();
  });
  await ready(page);
  await page.getByRole("button", { name: "Fit the graph to the view" }).click();
  await ready(page);
  await zoomTo(page, width === 1280 ? 0.2 : 0.3);
  await clearPointer(page);
  await check(
    page.locator(
      `[data-map-module="${boltons ? "boltons.iterutils" : "library.module_00"}"] [data-map-circle]`,
    ),
  ).toBeInViewport();
  await check(page.locator(".monaco-editor .view-lines").first()).toBeVisible();
  await capture("map-cone");
  // All real callers of is_iterable are inside its seed module; tests stay hidden.
  await check(page.locator("[data-map-distance]")).toHaveCount(boltons ? 0 : 3);
  await check(page.locator('[data-map-line="cone"]')).toHaveCount(boltons ? 0 : 4);
  await check(
    page
      .locator(".map-header")
      .filter({ hasText: boltons ? "12 of 12 in boltons" : "3 of 3 in tools" }),
  ).toBeVisible();
  await check(page.locator(`[data-map-module="${id}"] .map-name`)).toHaveCSS(
    "visibility",
    boltons ? "hidden" : "visible",
  );
  const badge = boltons ? null : await page.locator("[data-map-distance]").first().boundingBox();
  if (!boltons) expect(badge?.width).toBe(20);
  return badge?.width ?? null;
}
for (const width of [1920, 1280])
  for (const theme of ["dark", "day"] as const) {
    it(`captures evidence at ${String(width)} in ${theme}`, async () => {
      const page = await setup(width, theme, true);
      const pools: Record<string, Awaited<ReturnType<typeof measurePool>>> = {};
      const capture = async (name: string) => {
        await captureSweep(page, name, width, theme);
        if (name !== "group")
          pools[name] = await measurePool(page, boltons ? "boltons.strutils" : "library.module_01");
        if (process.env.MAP_LINES_SHOTS === "1")
          await page.screenshot({ path: `${shots}${name}-${String(width)}-${theme}.png` });
      };
      try {
        await page.getByRole("button", { name: "Fit the graph to the view" }).click();
        await ready(page);
        // Keep the synthetic group header below the summary overlay for a real pointer hover.
        if (!boltons && width === 1280) await zoomTo(page, 0.21);
        await clearPointer(page);
        expect((await camera(page)).level).toBe("map");
        await unobstructedNames(page);
        await capture("map");
        const id = boltons ? "boltons.typeutils" : "library.module_00",
          group = boltons ? "boltons" : "library";
        await pointAt(page, await modulePoint(page, id));
        await unobstructedNames(page);
        await capture("map-hover");
        await check(page.locator("[data-map-line]").first()).toBeVisible();
        const measured = await measureEvidence(page);
        await pointAt(page, await groupPoint(page, group));
        await fullAgentLabel(page, boltons ? "boltons.strutils" : "library.module_01");
        await unobstructedNames(page);
        await capture("map-hover-group");
        const click = await groupPoint(page, group);
        await page.mouse.click(click.x, click.y);
        await ready(page);
        await check(page.locator("[data-field-markers]").first()).toHaveCSS(
          "visibility",
          "visible",
        );
        await panCardEmitterIntoView(page);
        await ready(page);
        const groupZoom = (await camera(page)).zoom;
        const cardPool = await assertCardPool(page, theme);
        await capture("group");
        const badge = await captureCone(page, width, id, capture);
        if (sweep === "M3") await captureSurface(page, width, theme);
        if (process.env.MAP_LINES_SHOTS === "1")
          await writeFile(
            `${shots}measurements-${String(width)}-${theme}.json`,
            JSON.stringify(
              {
                ...measured,
                pools,
                badge,
                snapshot: boltons ? "boltons" : "synthetic",
                groupZoom,
                cardPool,
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
