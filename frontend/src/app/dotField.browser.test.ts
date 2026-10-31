import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import {
  browser,
  fieldPixels,
  lifeOf,
  lightMass,
  settledGraph,
  STRAY,
  withPage,
} from "./dotField.browser.support";

// Local review artifacts are opt-in; CI still runs every visual assertion below.
const captureShots = process.env.LIFE_SHOTS === "1";
const referenceFile = fileURLToPath(
  new URL("../../../.shots/life-look/reference/life.html", import.meta.url),
);

it("measures a frame at 1920 by 1080", async () => {
  // The lamps are computed on the CPU and drawn on the GPU; headless Chromium's GPU is a software
  // one, so the draw is an upper bound of what a real GPU takes.
  await withPage({ viewport: { width: 1920, height: 1080 } }, async (page) => {
    const cost = await page.evaluate(() => {
      const { field } = window.fieldTest.elements();
      const probe = field.oversightField;
      if (!probe) throw new Error("No probe");
      const runs = 60;
      const start = performance.now();
      // Each pixel call computes the lamps, uploads them, draws and reads back: a whole frame.
      for (let k = 0; k < runs; k += 1) probe.pixel(0, 0);
      return {
        browserFrameMs: (performance.now() - start) / runs,
        width: field.clientWidth,
        height: field.clientHeight,
        cells: Math.ceil(field.clientWidth / 7) * Math.ceil(field.clientHeight / 7),
      };
    });
    if (captureShots) {
      const dir = fileURLToPath(new URL("../../../.shots/", import.meta.url));
      mkdirSync(dir, { recursive: true });
      writeFileSync(`${dir}dot-field-frame-cost.json`, JSON.stringify(cost, null, 2));
    }
    expect(cost.cells).toBeGreaterThan(30_000);
    expect(Number.isFinite(cost.browserFrameMs)).toBe(true);
  });
}, 60_000);

const median = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
};
const p95 = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)] ?? 0;
};
const summary = (values: number[]) => ({
  samples: values.length,
  medianMs: Number(median(values).toFixed(2)),
  p95Ms: Number(p95(values).toFixed(2)),
});

/** The time of whole frames: lamps, ground, upload, draw and read back, one call to the probe each. */
function wholeFrames(page: Page, runs: number) {
  return page.evaluate((count) => {
    const probe = window.fieldTest.elements().field.oversightField;
    if (!probe) throw new Error("No probe");
    const times: number[] = [];
    for (let k = 0; k < count; k += 1) {
      const start = performance.now();
      probe.pixel(0, 0);
      times.push(performance.now() - start);
    }
    return times;
  }, runs);
}

it("measures the frame with contours at rest and during a merge travel, at 1920 by 1080", async () => {
  const viewport = { width: 1920, height: 1080 };
  const report: Record<string, unknown> = { viewport };
  const samples = {
    restWholeFrame: [] as number[],
    travelWholeFrame: [] as number[],
    afterlightWholeFrame: [] as number[],
    travelFrameCallback: [] as number[],
  };
  // Leave a minute for the last page and cleanup before the test's 180 s timeout.
  const deadline = Date.now() + 120_000;
  // The whole frame is a bound: it waits for the GPU. The callback is what the page's own frames cost.
  await withPage({ scene: "rest", viewport }, async (page) => {
    report.renderer = await page.evaluate(() => {
      const gl = document.createElement("canvas").getContext("webgl2");
      const info = gl?.getExtension("WEBGL_debug_renderer_info");
      return info ? String(gl?.getParameter(info.UNMASKED_RENDERER_WEBGL)) : "unknown";
    });
    expect((await lifeOf(page))?.contours).toBe(true);
    await wholeFrames(page, 10);
    samples.restWholeFrame.push(...(await wholeFrames(page, 100)));
  });
  // Frames drawn in real time while the pulse travels and then while the light fades.
  const measureTravel = async (page: Page) => {
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    const { travel, afterlight } = await page.evaluate(() => {
      const probe = window.fieldTest.elements().field.oversightField;
      if (!probe) throw new Error("No probe");
      const travel: number[] = [];
      const afterlight: number[] = [];
      const end = performance.now() + 3_000;
      while (performance.now() < end) {
        const start = performance.now();
        probe.pixel(0, 0);
        const took = performance.now() - start;
        const { pulses, lights } = probe.life();
        if (pulses > 0) travel.push(took);
        else if (lights > 0 && travel.length > 0) afterlight.push(took);
      }
      return { travel, afterlight };
    });
    samples.travelWholeFrame.push(...travel);
    samples.afterlightWholeFrame.push(...afterlight);
  };
  // The page's own frames while a pulse travels, in real time: the time inside each frame callback.
  const measureCallbacks = async (page: Page) => {
    await page.evaluate(() => {
      const times: number[] = [];
      Object.assign(window, { frameTimes: times });
      const request = window.requestAnimationFrame.bind(window);
      window.requestAnimationFrame = (callback) =>
        request((stamp) => {
          const start = performance.now();
          callback(stamp);
          times.push(performance.now() - start);
        });
    });
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    await page.waitForFunction(
      () => window.fieldTest.elements().field.oversightField?.life().pulses === 1,
    );
    await page.waitForFunction(
      () => window.fieldTest.elements().field.oversightField?.life().pulses === 0,
    );
    const times = await page.evaluate(
      () => (window as unknown as { frameTimes: number[] }).frameTimes,
    );
    samples.travelFrameCallback.push(...times);
  };
  const collection: Record<string, number>[] = [];
  let passes = 0;
  while (
    Object.values(samples).some((values) => values.length <= 10) &&
    passes < 8 &&
    Date.now() < deadline
  ) {
    passes += 1;
    if (samples.travelWholeFrame.length <= 10 || samples.afterlightWholeFrame.length <= 10)
      await withPage({ scene: "waiting", viewport }, measureTravel);
    if (samples.travelFrameCallback.length <= 10 && Date.now() < deadline)
      await withPage({ scene: "waiting", viewport }, measureCallbacks);
    const counts = {
      pass: passes,
      ...Object.fromEntries(Object.entries(samples).map(([key, values]) => [key, values.length])),
    };
    collection.push(counts);
    console.info("frame-cost", counts);
  }
  report.passes = passes;
  report.collection = collection;
  for (const [key, values] of Object.entries(samples)) report[key] = summary(values);
  if (captureShots) {
    const dir = fileURLToPath(new URL("../../../.shots/life-look/r4/", import.meta.url));
    mkdirSync(dir, { recursive: true });
    writeFileSync(`${dir}frame-cost.json`, JSON.stringify(report, null, 2));
  }
  for (const key of [
    "restWholeFrame",
    "travelWholeFrame",
    "afterlightWholeFrame",
    "travelFrameCallback",
  ])
    expect(
      (report[key] as { samples: number }).samples,
      `${key}: ${String((report[key] as { samples: number }).samples)} samples after ${String(passes)} passes (limit: 8 passes / 120 s)`,
    ).toBeGreaterThan(10);
}, 180_000);

/**
 * Puts one area of the approved sketch and one of the product's capture side by side in one image,
 * both at their own size, so lines and lamps compare pixel for pixel. The two layouts differ, so
 * each area is given by its own corner.
 */
async function beside(
  sketch: { path: string; x: number; y: number },
  shot: { path: string; x: number; y: number },
  out: string,
) {
  if (!browser) throw new Error("Browser was not started");
  const [width, height] = [960, 600];
  const area = ({ path, x, y }: { path: string; x: number; y: number }) =>
    `<img src="data:image/png;base64,${readFileSync(path).toString("base64")}" style="width:${String(width)}px;height:${String(height)}px;object-fit:none;object-position:-${String(x)}px -${String(y)}px">`;
  const page = await browser.newPage({ viewport: { width: width * 2, height } });
  try {
    await page.setContent(
      `<body style="margin:0;display:flex;background:#000">${area(sketch)}${area(shot)}</body>`,
    );
    await page.waitForFunction(() => [...document.images].every((image) => image.complete));
    writeFileSync(out, await page.screenshot());
  } finally {
    await page.close();
  }
}

/**
 * Lamps at levels 0, 1/3, 2/3 and 1 over the 7 px cells that nothing hides: not the rows and folded
 * cards (opaque: the childless nodes, by their drawn boxes alone), not the masks
 * (overlays and frame headers). The cells in a translucent frame's body, outside its rows, count.
 */
function lampHistogram(page: Page) {
  return page.evaluate(() => {
    const { graph, field } = window.fieldTest.elements();
    const probe = field.oversightField;
    const camera = graph.oversightGraph;
    if (!probe || !camera) throw new Error("No probe");
    const zoom = camera.zoom();
    const pan = camera.pan();
    const covers = [
      ...camera.nodeBounds(":childless", false).map((b) => ({
        x1: b.x1 * zoom + pan.x,
        y1: b.y1 * zoom + pan.y,
        x2: b.x2 * zoom + pan.x,
        y2: b.y2 * zoom + pan.y,
      })),
      ...probe.masks(),
    ];
    const counts = [0, 0, 0, 0];
    for (let y = 3.5; y < field.clientHeight; y += 7)
      for (let x = 3.5; x < field.clientWidth; x += 7) {
        if (covers.some((b) => x > b.x1 && x < b.x2 && y > b.y1 && y < b.y2)) continue;
        const level = probe.lamp(x, y)?.level ?? 0;
        counts[level] = (counts[level] ?? 0) + 1;
      }
    return counts;
  });
}

/** Renders the approved interactive reference at 1920 by 1080, still, in a state and a theme. */
async function referenceShot(
  state: "rest" | "watch" | "merge",
  theme: "dark" | "day",
  out: string,
) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  try {
    await page.goto(
      `file://${referenceFile}?theme=${theme}&${state === "merge" ? "freeze=merge&t=0.975" : "freeze=1"}#${state === "merge" ? "rest" : state}`,
    );
    await page.waitForFunction(
      () => typeof (window as unknown as { __ft?: number }).__ft === "number",
    );
    writeFileSync(out, await page.screenshot());
  } finally {
    await page.close();
  }
}

const viewport = { width: 1920, height: 1080 };
const shots = fileURLToPath(new URL("../../../.shots/life-look/r6/", import.meta.url));
const sketches = fileURLToPath(new URL("../../../docs/sketches/life/", import.meta.url));

function busyScene(
  scene: "watch" | "both",
  theme: "dark" | "day",
  test: (page: Page) => Promise<void>,
) {
  return withPage(
    { scene, viewport, reducedMotion: "reduce", ...(theme === "day" ? { theme } : {}) },
    async (page) => {
      // At Functions level, both agents' touched rows are visible, including folded classes.
      if (scene === "both")
        await page.getByRole("button", { name: "Functions", exact: true }).click();
      await settledGraph(page);
      await test(page);
    },
  );
}

it("checks busy lamp histograms in both themes at 1920 by 1080", async () => {
  const histograms: Record<string, number[]> = {};
  for (const scene of ["watch", "both"] as const)
    for (const theme of ["dark", "day"] as const)
      await busyScene(scene, theme, async (page) => {
        const counts = await lampHistogram(page);
        histograms[`${scene}-${theme}`] = counts;
        expect(counts).toHaveLength(4);
        expect(counts[0]).toBeGreaterThan(0);
        for (const lit of counts.slice(1)) expect(lit).toBeGreaterThan(0);
      });
  if (captureShots) {
    mkdirSync(shots, { recursive: true });
    writeFileSync(`${shots}histograms.json`, JSON.stringify(histograms));
  }
}, 120_000);

it("checks the fade curve at 1920 by 1080", async () => {
  await withPage({ scene: "waiting", viewport, clock: true }, async (page) => {
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    await page.clock.runFor(250 + 725);
    await settledGraph(page);
    // The decided fade, from the light's arrival on: how much light is left every five seconds.
    await page.clock.fastForward(725 + 350 + 1500);
    const fade: { t: number; mass: number; warm: number }[] = [];
    for (let t = 0; t <= 100; t += 5) {
      fade.push({ t, mass: await lightMass(page), warm: (await fieldPixels(page)).warm });
      await page.clock.fastForward(5_000);
    }
    if (captureShots) {
      mkdirSync(shots, { recursive: true });
      writeFileSync(`${shots}fade.json`, JSON.stringify(fade));
    }
    expect(fade.at(-1)?.warm).toBeLessThan(STRAY);
  });
}, 120_000);

const pair = (
  sketch: string,
  sketchAt: [number, number],
  shot: string,
  shotAt: [number, number],
  name: string,
) =>
  beside(
    { path: sketch, x: sketchAt[0], y: sketchAt[1] },
    { path: `${shots}${shot}.png`, x: shotAt[0], y: shotAt[1] },
    `${shots}compare-${name}.png`,
  );

it.runIf(captureShots)(
  "captures rest and busy at 1920 by 1080, dark and day, beside their sketches",
  async () => {
    mkdirSync(shots, { recursive: true });
    const shoot = async (page: Page, name: string) => {
      await settledGraph(page);
      writeFileSync(`${shots}${name}.png`, await page.screenshot());
    };
    await withPage({ scene: "rest", viewport }, (page) => shoot(page, "rest-dark"));
    await withPage({ scene: "rest", viewport, theme: "day" }, (page) => shoot(page, "rest-day"));
    for (const scene of ["watch", "both"] as const)
      for (const theme of ["dark", "day"] as const)
        await busyScene(scene, theme, (page) =>
          shoot(page, `busy-${scene === "both" ? "both-" : ""}${theme}`),
        );
    for (const theme of ["dark", "day"] as const) {
      await withPage(
        { scene: "waiting", viewport, clock: true, ...(theme === "day" ? { theme } : {}) },
        async (page) => {
          // Events 22 and 23 are 160ms and 170ms after the merge: carry their actual age.
          await page.evaluate(() => {
            window.fieldTest.advance(23);
          });
          await page.clock.runFor(250 + 725 - 170);
          await shoot(page, `merge-mid-${theme}`);
        },
      );
    }
    await pair(`${sketches}rest-contour.png`, [60, 200], "rest-dark", [560, 400], "rest-dark");
    await pair(`${sketches}rest-day.png`, [60, 200], "rest-day", [560, 400], "rest-day");
    await pair(
      `${sketches}merge-mid.png`,
      [480, 200],
      "merge-mid-dark",
      [560, 200],
      "merge-mid-dark",
    );
  },
  240_000,
);

it.runIf(captureShots && existsSync(referenceFile))(
  "pairs busy captures with the optional interactive reference",
  async () => {
    await referenceShot("rest", "dark", `${shots}reference-rest-dark.png`);
    await referenceShot("rest", "day", `${shots}reference-rest-day.png`);
    await referenceShot("watch", "dark", `${shots}reference-busy-dark.png`);
    await referenceShot("watch", "day", `${shots}reference-busy-day.png`);
    for (const theme of ["dark", "day"] as const) {
      await referenceShot("merge", theme, `${shots}reference-merge-mid-${theme}.png`);
      await pair(
        `${shots}reference-merge-mid-${theme}.png`,
        [480, 200],
        `merge-mid-${theme}`,
        [560, 200],
        `reference-merge-mid-${theme}`,
      );
      const sketch = `${shots}reference-busy-${theme}.png`;
      await pair(sketch, BUSY_AT.sketch, `busy-${theme}`, BUSY_AT.shot, `busy-${theme}`);
      // Both agents: each gets its own window because the product's lights sit further apart.
      await pair(sketch, BUSY_AT.sketch, `busy-both-${theme}`, [60, 300], `busy-both-${theme}-b`);
      await pair(sketch, BUSY_AT.sketch, `busy-both-${theme}`, [940, 430], `busy-both-${theme}-a`);
    }
  },
  120_000,
);

/** Where each busy capture's lit node sits: the corner of the area compared, in px. */
const BUSY_AT = { sketch: [480, 200] as [number, number], shot: [560, 200] as [number, number] };
