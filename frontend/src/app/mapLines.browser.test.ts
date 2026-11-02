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
    '<html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { mountEvidence, reviewEvidence, demoEvidence, evidenceTaps, stationaryEvidence } from "/src/app/mapLines.browser.fixture.tsx"; window.mapLinesTest = { mount: mountEvidence, review: reviewEvidence, demo: demoEvidence, taps: evidenceTaps, stationary: stationaryEvidence };</script></body></html>',
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
          ? [{ agent: agent.textContent, label: label.textContent }]
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
        // Give the synthetic capture's expanded agent labels room between columns.
        if (!boltons && width === 1280) await zoomTo(page, 0.25);
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
        await capture("map-hover-group");
        const click = await groupPoint(page, group);
        await page.mouse.click(click.x, click.y);
        await ready(page);
        await check(page.locator("[data-field-markers]").first()).toHaveCSS(
          "visibility",
          "visible",
        );
        const groupZoom = (await camera(page)).zoom;
        await capture("group");
        const badge = await captureCone(page, width, id, capture);
        if (process.env.MAP_LINES_SHOTS === "1")
          await writeFile(
            `${shots}measurements-${String(width)}-${theme}.json`,
            JSON.stringify(
              {
                ...measured,
                badge,
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
