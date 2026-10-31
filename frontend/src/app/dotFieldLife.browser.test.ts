import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import {
  browser,
  fieldPixels,
  lifeOf,
  lightMass,
  look,
  settledGraph,
  STRAY,
  withPage,
} from "./dotField.browser.support";

const viewport = { width: 1920, height: 1080 };

/** The graph's area as a PNG, with the field as it stands. */
async function groundShot(page: Page) {
  await settledGraph(page);
  const rect = await page.evaluate(() => {
    const { x, y, width, height } = window.fieldTest.elements().graph.getBoundingClientRect();
    return { x, y, width, height };
  });
  return page.screenshot({ clip: rect });
}

/** How far two shots of the same area differ: the sum of the channel differences of their pixels. */
function differing(page: Page, a: Buffer, b: Buffer) {
  return page.evaluate(
    async ({ first, second }) => {
      const decode = async (base64: string) => {
        const bitmap = await createImageBitmap(
          new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))]),
        );
        const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("No 2d context");
        context.drawImage(bitmap, 0, 0);
        return context.getImageData(0, 0, bitmap.width, bitmap.height).data;
      };
      const [x, y] = [await decode(first), await decode(second)];
      let sum = 0;
      for (let at = 0; at < x.length; at += 4)
        sum +=
          Math.abs((x[at] ?? 0) - (y[at] ?? 0)) +
          Math.abs((x[at + 1] ?? 0) - (y[at + 1] ?? 0)) +
          Math.abs((x[at + 2] ?? 0) - (y[at + 2] ?? 0));
      return sum;
    },
    { first: a.toString("base64"), second: b.toString("base64") },
  );
}

it("draws the rest ground: gray lines, no colored light, and nothing under an overlay", async () => {
  await withPage({ scene: "rest", reducedMotion: "reduce" }, async (page) => {
    expect(await lifeOf(page)).toEqual({ pulses: 0, lights: 0, contours: true });
    const seen = await fieldPixels(page);
    // Lines over the empty ground, in every channel alike, and none of agent A's orange.
    expect(seen.changed).toBeGreaterThan(2000);
    expect(seen.colored).toBe(0);
    expect(seen.warm).toBe(0);
    // The mask holds: no line shows through the title, the legend, the zoom controls or a panel.
    expect(seen.inside).toBe(0);
  });
}, 60_000);

it("draws the lines at the approved sketch's level on the #070707 ground", async () => {
  await withPage(
    { scene: "rest", reducedMotion: "reduce", viewport: { width: 1920, height: 1080 } },
    async (page) => {
      await settledGraph(page);
      const png = await page.screenshot({ clip: { x: 60, y: 520, width: 940, height: 420 } });
      const seen = await page.evaluate(async (base64) => {
        const bitmap = await createImageBitmap(
          new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))]),
        );
        const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("No 2d context");
        context.drawImage(bitmap, 0, 0);
        const data = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
        const values: number[] = [];
        for (let at = 0; at < data.length; at += 4)
          values.push(Math.max(data[at] ?? 0, data[at + 1] ?? 0, data[at + 2] ?? 0));
        values.sort((a, b) => a - b);
        const lines = values.filter((value) => value > 12);
        return {
          ground: values[Math.floor(values.length / 4)],
          brightest: values[Math.floor(values.length * 0.99)],
          median: lines[Math.floor(lines.length / 2)],
          coverage: lines.length / values.length,
        };
      }, png.toString("base64"));
      // The ground is #070707; the sketch's brightest lines are 25 to 28 on it, its median line about
      // 23, and its lines cover 6 to 7% of a clear area (it was 11, 9 and 2.7% on black).
      expect(seen.ground).toBe(7);
      expect(seen.brightest).toBeGreaterThanOrEqual(22);
      expect(seen.brightest).toBeLessThanOrEqual(32);
      expect(seen.median).toBeGreaterThanOrEqual(18);
      expect(seen.coverage).toBeGreaterThan(0.035);
      // Old bound 0.09 → 0.105: new-start coverage 0.09675 + 0.00825 margin.
      expect(seen.coverage).toBeLessThan(0.105);
    },
  );
}, 60_000);

it("still draws the lines in the day theme, as ink on paper", async () => {
  await withPage({ scene: "rest", reducedMotion: "reduce" }, async (page) => {
    await page.evaluate(() => {
      document.documentElement.dataset.theme = "day";
    });
    await page.waitForFunction(
      () => window.fieldTest.elements().field.oversightField?.life().contours === true,
    );
    const seen = await fieldPixels(page);
    expect(seen.changed).toBeGreaterThan(2000);
    expect(seen.colored).toBe(0);
    expect(seen.inside).toBe(0);
  });
}, 60_000);

it("keeps the lines still under reduced motion, and lets them drift otherwise, without frames", async () => {
  await withPage({ scene: "rest", reducedMotion: "reduce", clock: true }, async (page) => {
    const before = await groundShot(page);
    await page.clock.fastForward(240_000);
    expect(await differing(page, before, await groundShot(page))).toBe(0);
    expect((await lifeOf(page))?.contours).toBe(true);
  });
  await withPage({ scene: "rest", clock: true }, async (page) => {
    const draws = () =>
      page.evaluate(() => window.fieldTest.elements().field.oversightField?.draws() ?? 0);
    const still = await draws();
    await page.clock.runFor(2_000);
    // The slow timer repaints, and no animation frame is running: the drift is not a loop.
    expect(await draws()).toBeGreaterThanOrEqual(still + 3);
    expect(
      await page.evaluate(() => window.fieldTest.elements().field.oversightField?.animating()),
    ).toBe(false);
    const first = await groundShot(page);
    await page.clock.runFor(1_000);
    const second = await groundShot(page);
    await page.clock.fastForward(240_000);
    const third = await groundShot(page);
    const seconds = await differing(page, first, second);
    const minutes = await differing(page, first, third);
    // A second moves the ground by a hair; minutes change its shape.
    expect(minutes).toBeGreaterThan(500);
    expect(seconds).toBeLessThan(minutes / 2);
  });
}, 120_000);

it("sends a merge's pulse along the graph to its node, then lights it and fades the light", async () => {
  await withPage({ scene: "waiting", clock: true }, async (page) => {
    const nodeCenters = () =>
      page.evaluate(() => window.fieldTest.elements().graph.oversightGraph?.nodeCenters());
    const camera = () =>
      page.evaluate(() => {
        const graph = window.fieldTest.elements().graph.oversightGraph;
        return [graph?.zoom(), graph?.pan(), document.activeElement?.tagName];
      });
    // The change waits on an open entry: its steady light is on before the decision.
    expect(await lifeOf(page)).toMatchObject({ pulses: 0, lights: 0 });
    const waiting = await lightMass(page);
    const waitingNode = (await look(page)).near?.level;
    expect(waiting).toBeGreaterThan(50);
    expect((await fieldPixels(page)).warm).toBeGreaterThan(200);
    const start = await page.evaluate(() => {
      const { field } = window.fieldTest.elements();
      const origin = field.getBoundingClientRect();
      const card = document.querySelector('[data-agent-origin="A"][data-slot="A"]');
      if (!card) throw new Error("No open agent A card");
      const rect = card.getBoundingClientRect();
      // Watch keeps the original graph-box clamp, even when the card is outside the canvas.
      return {
        x: Math.min(field.clientWidth, Math.max(0, rect.left - origin.left)),
        y: Math.min(field.clientHeight, Math.max(0, rect.top + rect.height / 2 - origin.top)),
      };
    });
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    // The merge is told, but the pulse waits a moment before it leaves the card.
    expect((await lifeOf(page))?.pulses).toBe(0);
    await page.clock.runFor(250 + 16);
    const departure = await page.evaluate(() => {
      const paths = document.querySelectorAll<SVGPathElement>("[data-field-travel] g path");
      const tail = [...paths].filter((path) => path.getAttribute("d")).at(-1);
      const point = tail?.getPointAtLength(0);
      if (!point) throw new Error("No departing watch pulse");
      return { x: point.x, y: point.y };
    });
    expect(
      Math.abs(departure.x - start.x),
      JSON.stringify({ departure, start }),
    ).toBeLessThanOrEqual(2);
    expect(
      Math.abs(departure.y - start.y),
      JSON.stringify({ departure, start }),
    ).toBeLessThanOrEqual(2);
    await page.clock.runFor(725 - 16);
    expect((await lifeOf(page))?.pulses).toBe(1);
    // The decision itself settles the graph (the node's pending look goes); from here on, the pulse
    // and the light must leave the camera and the nodes exactly where they are.
    const still = [await camera(), await nodeCenters()];
    const mid = await page.evaluate(() => {
      const layer = document.querySelector("[data-field-travel]");
      const head = layer?.querySelector("circle");
      return {
        clipped: layer?.getAttribute("clip-path"),
        pieces: [...(layer?.querySelectorAll("path[stroke]") ?? [])]
          .map((path) => path.getAttribute("d") ?? "")
          .filter((d) => d !== "").length,
        stroke: layer?.querySelector("path[stroke]")?.getAttribute("stroke"),
        color: getComputedStyle(document.documentElement).getPropertyValue("--agent-a").trim(),
        head: [head?.getAttribute("cx"), head?.getAttribute("cy")],
        pointer: layer ? getComputedStyle(layer).pointerEvents : null,
      };
    });
    expect(mid.clipped).toMatch(/^url\(#field-travel-/);
    expect(mid.pieces).toBeGreaterThan(2);
    expect(mid.stroke?.toLowerCase()).toBe(mid.color.toLowerCase());
    expect(mid.pointer).toBe("none");
    expect(mid.head.every((value) => Number.isFinite(Number(value)))).toBe(true);
    // The destination keeps its waiting level; the travelling gaussian adds its own LED patch.
    expect((await look(page)).near?.level).toBe(waitingNode);
    const travelling = await lightMass(page);
    expect(travelling).toBeGreaterThan(waiting * 1.1);
    // Never moves the camera, the focus or a node.
    expect([await camera(), await nodeCenters()]).toEqual(still);
    // The pulse arrives at the node, and its trail fades; then the node's light is up and warm.
    await page.clock.fastForward(725 + 350 + 1500);
    expect(await lifeOf(page)).toMatchObject({ pulses: 0, lights: 1 });
    expect(
      await page.evaluate(() => document.querySelectorAll("[data-field-travel] g").length),
    ).toBe(0);
    const lit = await lightMass(page);
    expect(lit).toBeGreaterThan(waiting * 0.9);
    expect(lit).toBeLessThan(waiting * 1.1);
    // Soft: the light is not a block, its colour stays off the overlays, and it fades as time goes:
    // 20 s in, its halo outside the card is still lit and thinner; the cut ends it before the 90 s.
    await page.clock.fastForward(20_000);
    const half = await fieldPixels(page);
    expect(half.warm).toBeGreaterThan(0);
    // Old ratio 0.8 → 0.85: new-start mass 45/55 = 0.81818 + 0.03182 margin.
    expect(await lightMass(page)).toBeLessThan(lit * 0.85);
    expect(half.inside).toBe(0);
    await page.clock.fastForward(71_000);
    expect(await lifeOf(page)).toMatchObject({ pulses: 0, lights: 0 });
    expect((await fieldPixels(page)).warm).toBeLessThan(STRAY);
    expect([await camera(), await nodeCenters()]).toEqual(still);
  });
}, 120_000);

/** Optional review evidence: the shot tool records stills only, so retain three travel moments. */
async function captureReviewMerge(page: Page, theme: string, out: string, geometry: unknown) {
  await page.screenshot({ path: `${out}${theme}-mid.png` });
  writeFileSync(`${out}${theme}-geometry.json`, JSON.stringify(geometry, null, 2));
  await page.clock.runFor(900 - 725);
  await page.screenshot({ path: `${out}${theme}-0.9s.png` });
  await page.clock.runFor(600);
  await page.screenshot({ path: `${out}${theme}-1.5s.png` });
  if (!browser) throw new Error("Browser was not started");
  const comparison = await browser.newPage({ viewport: { width: 3840, height: 1080 } });
  try {
    const images = [
      fileURLToPath(new URL("../../../docs/sketches/life/merge-mid.png", import.meta.url)),
      `${out}${theme}-mid.png`,
    ].map(
      (path) =>
        `<img width="1920" height="1080" src="data:image/png;base64,${readFileSync(path).toString("base64")}">`,
    );
    await comparison.setContent(`<body style="margin:0;display:flex">${images.join("")}</body>`);
    await comparison.waitForFunction(() => [...document.images].every((image) => image.complete));
    await comparison.screenshot({ path: `${out}${theme}-compare.png` });
  } finally {
    await comparison.close();
  }
}

it.each(["dark", "day"] as const)(
  "starts a review merge at the folded agent letter in %s without moving the view",
  async (theme) => {
    await withPage(
      { scene: "review", viewport, clock: true, ...(theme === "day" ? { theme } : {}) },
      async (page) => {
        await page.getByRole("button", { name: "Close review", exact: true }).focus();
        const focus = await page.evaluateHandle(() => document.activeElement);
        const view = () =>
          page.evaluate(() => {
            const graph = window.fieldTest.elements().graph.oversightGraph;
            // Compound frame centers can change when the pending badge disappears.
            return { zoom: graph?.zoom(), pan: graph?.pan(), nodes: graph?.positions() };
          });
        const before = await view();
        const letter = await page
          .getByRole("group", { name: "Agent A: Ready for review", exact: true })
          .locator(":scope > span")
          .first()
          .boundingBox();
        if (!letter) throw new Error("No folded agent A letter");
        expect((await lifeOf(page))?.pulses).toBe(0);
        await page.evaluate(() => {
          window.fieldTest.advance(21);
        });
        expect((await lifeOf(page))?.pulses).toBe(0);
        // Only the last 100 px of the route are drawn. Read its origin on the first travelling
        // frame, before that tail leaves the letter, then check the same pulse at mid-travel.
        await page.clock.runFor(250 + 16);
        const departure = await page.evaluate(() => {
          const group = document.querySelector("[data-field-travel] g");
          const paths = [...(group?.querySelectorAll<SVGPathElement>("path") ?? [])];
          const tail = paths.filter((path) => path.getAttribute("d")).at(-1);
          const point = tail?.getPointAtLength(0);
          const box = window.fieldTest.elements().field.getBoundingClientRect();
          if (!point) throw new Error("No departing pulse");
          return { x: point.x + box.left, y: point.y + box.top };
        });
        await page.clock.runFor(300 - 16);
        const out = fileURLToPath(new URL("../../../.shots/review-merge-travel/", import.meta.url));
        const capture = process.env.REVIEW_MERGE_SHOTS === "1";
        if (capture) {
          mkdirSync(out, { recursive: true });
          await page.screenshot({ path: `${out}${theme}-0.3s.png` });
        }
        await page.clock.runFor(725 - 300);
        expect((await lifeOf(page))?.pulses).toBe(1);
        const mid = await page.evaluate(() => {
          const groups = document.querySelectorAll("[data-field-travel] g");
          return {
            count: groups.length,
            stroke: groups[0]?.querySelector("path[stroke]")?.getAttribute("stroke"),
            color: getComputedStyle(document.documentElement).getPropertyValue("--agent-a").trim(),
          };
        });
        expect(mid.count).toBe(1);
        expect(mid.stroke?.toLowerCase()).toBe(mid.color.toLowerCase());
        expect(await view()).toEqual(before);
        expect(await focus.evaluate((element) => element === document.activeElement)).toBe(true);
        await browserExpect(
          page.getByRole("complementary", { name: "Review panel" }),
        ).toBeVisible();
        if (capture) await captureReviewMerge(page, theme, out, { departure, letter, mid });
        expect(
          Math.abs(departure.x - letter.x),
          JSON.stringify({ departure, letter }),
        ).toBeLessThanOrEqual(2);
        expect(
          Math.abs(departure.y - letter.y - letter.height / 2),
          JSON.stringify({ departure, letter }),
        ).toBeLessThanOrEqual(2);
      },
    );
  },
  60_000,
);

it.each(["dark", "day"] as const)(
  "lights square LEDs around the travelling head in %s, excluding drawn masks",
  async (theme) => {
    await withPage(
      { scene: "waiting", clock: true, ...(theme === "day" ? { theme } : {}) },
      async (page) => {
        await page.evaluate(() => {
          window.fieldTest.advance(23);
        });
        await page.clock.runFor(250 + 725 - 170);
        const patch = await page.evaluate(() => {
          const head = document.querySelector("[data-field-travel] circle");
          const probe = window.fieldTest.elements().field.oversightField;
          if (!head || !probe) throw new Error("Missing travelling head");
          const x = Number(head.getAttribute("cx"));
          const y = Number(head.getAttribute("cy"));
          let lit = 0;
          let orange = 0;
          let mass = 0;
          for (let cy = Math.floor((y - 19) / 7) * 7 + 3.5; cy < y + 19; cy += 7)
            for (let cx = Math.floor((x - 19) / 7) * 7 + 3.5; cx < x + 19; cx += 7) {
              const lamp = probe.lamp(cx, cy);
              mass += lamp?.level ?? 0;
              if (lamp && lamp.level > 0) {
                lit++;
                if (lamp.color[0] > lamp.color[2]) orange++;
              }
            }
          const bounds = window.fieldTest.elements().field.getBoundingClientRect();
          const mask = document.createElement("div");
          mask.dataset.fieldMask = "pulse-test";
          mask.style.cssText = `position:fixed;pointer-events:none;left:${String(bounds.left + x - 10)}px;top:${String(bounds.top + y - 10)}px;width:21px;height:21px`;
          document.querySelector(".shell-frame")?.append(mask);
          return { x, y, lit, orange, mass };
        });
        expect(patch.lit).toBeGreaterThan(10);
        expect(patch.orange).toBe(patch.lit);
        const masked = await page.evaluate(({ x, y }) => {
          const probe = window.fieldTest.elements().field.oversightField;
          const pixel = probe?.pixel(x, y);
          return { pixel, lamp: probe?.lamp(x, y)?.level };
        }, patch);
        expect(masked.lamp).toBe(0);
        expect(masked.pixel?.[3]).toBe(0);
        await page.evaluate(() =>
          document.querySelector('[data-field-mask="pulse-test"]')?.remove(),
        );
        await page.emulateMedia({ reducedMotion: "reduce" });
        await browserExpect
          .poll(async () => {
            await page.clock.runFor(16);
            return page.evaluate(
              () => window.fieldTest.elements().field.oversightField?.life().pulses,
            );
          })
          .toBe(0);
        const still = await page.evaluate(({ x, y }) => {
          const probe = window.fieldTest.elements().field.oversightField;
          let mass = 0;
          for (let cy = Math.floor((y - 19) / 7) * 7 + 3.5; cy < y + 19; cy += 7)
            for (let cx = Math.floor((x - 19) / 7) * 7 + 3.5; cx < x + 19; cx += 7)
              mass += probe?.lamp(cx, cy)?.level ?? 0;
          return { mass, pulses: probe?.life().pulses };
        }, patch);
        expect(still.pulses).toBe(0);
        // Separate the travelling patch from the destination's existing waiting light.
        expect(patch.mass).toBeGreaterThan(still.mass);
      },
    );
  },
  60_000,
);

it("has no travel under reduced motion: the light stays where it was and fades as long", async () => {
  await withPage({ scene: "waiting", reducedMotion: "reduce", clock: true }, async (page) => {
    const waiting = await lightMass(page);
    expect(waiting).toBeGreaterThan(50);
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    await page.clock.runFor(100);
    expect(await lifeOf(page)).toMatchObject({ pulses: 0, lights: 1 });
    expect(
      await page.evaluate(() => document.querySelectorAll("[data-field-travel] g").length),
    ).toBe(0);
    const lit = await lightMass(page);
    expect(lit).toBeGreaterThan(waiting * 0.9);
    expect(lit).toBeLessThan(waiting * 1.1);
    await page.clock.fastForward(20_000);
    // The lamps thin out as the light fades; some are still lit, and fewer than before.
    expect((await fieldPixels(page)).warm).toBeGreaterThan(0);
    expect(await lightMass(page)).toBeLessThan(lit * 0.8);
    await page.clock.fastForward(71_000);
    expect(await lifeOf(page)).toMatchObject({ lights: 0 });
    expect((await fieldPixels(page)).warm).toBeLessThan(STRAY);
  });
}, 120_000);

/** The lamp level at the lit node while its agent is editing: the strongest light the field shows. */
async function workingLevel() {
  let level = 0;
  await withPage({ scene: "watch", reducedMotion: "reduce" }, async (page) => {
    level = (await look(page)).near?.level ?? 0;
  });
  return level;
}

it("holds a steady, dimmer light while the change waits on an inbox entry, however long", async () => {
  await withPage({ scene: "waiting", clock: true }, async (page) => {
    const working = await workingLevel();
    const seen = await look(page);
    expect(seen.near?.level).toBeGreaterThan(0);
    expect(seen.near?.level).toBeLessThan(working);
    const lit = await lightMass(page);
    expect(lit).toBeGreaterThan(50);
    expect((await fieldPixels(page)).warm).toBeGreaterThan(200);
    // No ring and no frames: the light has no motion, and a long wait changes nothing.
    expect(
      await page.evaluate(() => window.fieldTest.elements().field.oversightField?.animating()),
    ).toBe(false);
    await page.clock.fastForward(600_000);
    expect(await lifeOf(page)).toEqual({ pulses: 0, lights: 0, contours: true });
    expect(await lightMass(page)).toBe(lit);
  });
}, 60_000);

it("keeps the waiting light in review, and shows none once the change is done", async () => {
  await withPage({ scene: "review", reducedMotion: "reduce" }, async (page) => {
    expect((await look(page)).near?.level).toBeGreaterThan(0);
    expect((await fieldPixels(page)).warm).toBeGreaterThan(200);
  });
  await withPage({ scene: "rest", reducedMotion: "reduce" }, async (page) => {
    expect((await look(page)).near?.level).toBe(0);
    expect((await fieldPixels(page)).warm).toBeLessThan(STRAY);
  });
}, 60_000);
