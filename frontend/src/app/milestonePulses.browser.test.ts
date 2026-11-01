// Real Frame + WebGL field, with a held browser clock and event envelopes from the reducer.
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Browser, Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { beforeAll, afterAll, expect, it } from "vitest";
import type { FieldProbe, GraphProbe } from "../graph";
import { launchBrowser } from "../browserLaunch.support";
const out = fileURLToPath(new URL("../../../.shots/milestone-pulses/", import.meta.url));
let server: ViteDevServer, browser: Browser, url: string;
const fixture = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { Frame } from '/src/app/Frame.tsx';
import { initialView } from '/src/app/view.ts';
import { applyEvent, createInitialState, stateStore } from '/src/state/index.ts';
import raw from '/@fs/${fileURLToPath(new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url))}?raw';
const events = raw.trim().split('\\n').map(JSON.parse);
const root = createRoot(document.getElementById('root'));
const graph = () => document.querySelector('[aria-label="Repository graph"]');
let state = createInitialState();
function show() { stateStore.setState({ state }); flushSync(() => root.render(React.createElement(Frame, {
  state, view: initialView, onNodeTap() {}, canOpenChange() { return false; }, onOpenChange() {}, onClosePanel() {}
}))); }
window.milestones = {
  probes() { return { graph: graph().oversightGraph, field: graph().parentElement.querySelector('canvas').oversightField }; },
  mount() { state = events.filter(e => e.seq <= 20).reduce(applyEvent, createInitialState()); show(); },
  crowd() {
    for (const [i, slot] of ['B', 'C', 'D', 'E'].entries()) {
      const id = 'crowd-' + slot;
      const original = state.changes['change-1'];
      state = { ...state, agents: { ...state.agents, [id]: { ...state.agents['agent-1'], agent_id: id, slot, change_id: id } },
        changes: { ...state.changes, [id]: { ...original, change_id: id, agent_id: id, resolution: null,
          graph: { ...original.graph, nodes_modified: ['crowd-node-' + slot] } } },
        decisionEvents: [...state.decisionEvents, { seq: 30 + i, ts: state.clock, type: 'error', payload: {
          source: 'git', agent_id: id, change_id: id, fatal: false,
          message: 'Needs you — revision=1; head=abc; code=main_moved; reason=Main moved'
        }}] };
    }
    show();
  },
  send(kind, overlap = false) {
    if (overlap) {
      const agent = { ...state.agents['agent-1'], agent_id: 'agent-2', slot: 'B', state: 'ready_for_review', change_id: 'change-2' };
      state = { ...state, agents: { ...state.agents, 'agent-2': agent }, decisionEvents: [...state.decisionEvents, { ...events.find(e => e.seq === 19), payload: { ...events.find(e => e.seq === 19).payload, change_id: 'change-2', agent_id: 'agent-2' } }],
        changes: { ...state.changes, 'change-2': { ...state.changes['change-1'], change_id: 'change-2', agent_id: 'agent-2', proposal: { ...state.changes['change-1'].proposal, change_id: 'change-2', agent_id: 'agent-2' }, graph: { ...state.changes['change-1'].graph, nodes_modified: ['shop.pricing:price_breaks'] } } } };
    }
    if (kind === 'ready' || kind === 'overlap') state = applyEvent(state, { ...events.find(e => e.seq === 20), seq: 21 });
    if (kind === 'needs') state = applyEvent(state, { seq: 21, ts: events.find(e => e.seq === 20).ts, type: 'error', payload: {
      source: 'git', agent_id: 'agent-1', change_id: 'change-1', fatal: false,
      message: 'Needs you — revision=1; head=' + state.changes['change-1'].proposal.head_commit + '; code=main_moved; reason=Main moved'
    }});
    if (kind === 'merged') state = applyEvent(state, { ...events.find(e => e.seq === 21), seq: state.lastSeq + 1, ts: state.clock });
    show();
  }
};
`;
declare global {
  interface Window {
    milestones: {
      mount(): void;
      send(kind: string, overlap?: boolean): void;
      crowd(): void;
      probes(): { graph?: GraphProbe; field?: FieldProbe };
    };
  }
}
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${out}vite-cache`,
    server: {
      host: "127.0.0.1",
      port: 0,
      fs: { allow: [fileURLToPath(new URL("../../../", import.meta.url))] },
    },
  });
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser({ webGL: true, softwareRendering: true });
  mkdirSync(out, { recursive: true });
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function scene(
  theme: string,
  width: number,
  height: number,
  test: (page: Page) => Promise<void>,
  reduced = false,
  held = true,
) {
  const page = await browser.newPage({
    viewport: { width, height },
    reducedMotion: reduced ? "reduce" : "no-preference",
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    if (held) await page.clock.install();
    const html = await server.transformIndexHtml(
      "/",
      `<html><body><div id="root"></div><script type="module">import "/src/theme/index.css"; ${fixture}</script></body></html>`,
    );
    await page.route(/\?field-time=2$/, (route) =>
      route.fulfill({ contentType: "text/html", body: html }),
    );
    await page.goto(`${url}?field-time=2`);
    await page.waitForFunction(() => "milestones" in window);
    await page.evaluate((name) => {
      document.documentElement.dataset.theme = name;
      window.milestones.mount();
    }, theme);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => {
      const { graph, field } = window.milestones.probes();
      return !!field && (graph?.nodeCenters().length ?? 0) > 0 && graph?.animating() === false;
    });
    await page.getByRole("button", { name: "Zoom out", exact: true }).click();
    if (held) await page.clock.runFor(300);
    if (held) await page.clock.pauseAt((await page.evaluate(() => Date.now())) + 1000);
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}
async function ringLamps(page: Page) {
  return page.evaluate(() => {
    const { graph, field } = window.milestones.probes();
    const row = graph?.renderedBox("shop.pricing:compute_price");
    if (!field || !row) throw new Error("Missing field or row");
    const cx = (row.x1 + row.x2) / 2,
      cy = (row.y1 + row.y2) / 2,
      radius = (row.x2 - row.x1) / 2;
    const levels = Array.from({ length: 180 }, (_, i) => {
      const angle = (2 * Math.PI * i) / 180;
      const x = cx + radius * Math.cos(angle),
        y = cy + radius * Math.sin(angle);
      return y < row.y1 || y > row.y2 || x < row.x1 || x > row.x2
        ? (field.lamp(x, y)?.level ?? 0)
        : 0;
    });
    return [levels, document.activeElement?.tagName, graph?.nodeCenters()] as const;
  });
}
it("lights LEDs outside the row on Ready's first frame and keeps the graph and focus still", async () => {
  await scene("dark", 1920, 1080, async (page) => {
    const before = await ringLamps(page);
    await page.evaluate(() => {
      window.milestones.send("ready");
    });
    const after = await ringLamps(page);
    expect(after[0].some((v, i) => v > (before[0][i] ?? 0))).toBe(true);
    expect(after.slice(1)).toEqual(before.slice(1));
    await page.clock.runFor(710);
  });
}, 60_000);
const pill = (page: Page, word: string) =>
  page.getByLabel("Agent A", { exact: true }).getByText(word, { exact: true }).count();
it("holds the pending node and Merging card until arrival, then draws the masked halo and Merged", async () => {
  await scene("dark", 1920, 1080, async (page) => {
    await page.evaluate(() => {
      window.milestones.send("merged");
    });
    expect(await pill(page, "Merging")).toBe(1);
    await page.clock.runFor(1699);
    expect(await page.locator("[data-merge-halo]").count()).toBe(0);
    await page.clock.runFor(17);
    expect(await pill(page, "Merged")).toBe(1);
    expect(await page.locator("[data-merge-halo]").count()).toBe(1);
    expect(await page.locator("[data-field-travel]").getAttribute("clip-path")).toContain("url(");
  });
}, 60_000);
it("keeps travel and arrival together when Merged replaces Needs you", async () => {
  await scene("dark", 1920, 1080, async (page) => {
    await page.evaluate(() => {
      window.milestones.send("needs");
    });
    await page.clock.runFor(200);
    await page.evaluate(() => {
      window.milestones.send("merged");
    });
    await page.clock.runFor(500);
    expect(await pill(page, "Merging")).toBe(1);
    expect(await page.locator("[data-field-travel] g path[d]").count()).toBeGreaterThan(0);
    await page.clock.runFor(1216);
    expect(await pill(page, "Merged")).toBe(1);
    expect(await page.locator("[data-merge-halo]").count()).toBe(1);
  });
}, 60_000);
it("drops travel, the Merging label and the halo together at the pulse cap", async () => {
  await scene("dark", 1920, 1080, async (page) => {
    await page.evaluate(() => {
      window.milestones.send("merged");
    });
    expect(await pill(page, "Merging")).toBe(1);
    await page.evaluate(() => {
      window.milestones.crowd();
    });
    expect(await pill(page, "Merged")).toBe(1);
    await page.clock.runFor(500);
    expect(await page.locator("[data-field-travel] g path[d]").count()).toBe(0);
    await page.clock.runFor(1216);
    expect(await page.locator("[data-merge-halo]").count()).toBe(0);
  });
}, 60_000);
it.runIf(process.env.MILESTONE_SHOTS === "1")(
  "captures milestones, collisions, arrival and reduced motion with held clocks",
  async () => {
    const costs: Record<string, unknown> = {};
    for (const theme of ["dark", "day"])
      for (const width of [1920, 1280]) {
        const height = width === 1920 ? 1080 : 800;
        for (const kind of ["ready", "needs", "merged"])
          await scene(theme, width, height, async (page) => {
            await page.evaluate((kind) => {
              window.milestones.send(kind);
            }, kind);
            await page.clock.runFor(kind === "merged" ? 975 : kind === "needs" ? 240 : 250);
            await page.screenshot({ path: `${out}${kind}-${String(width)}-${theme}.png` });
            if (width === 1920 && theme === "dark") {
              const cdp = await page.context().newCDPSession(page);
              const { data } = await cdp.send("Page.captureScreenshot", {
                format: "png",
                clip: { x: 0, y: 0, width, height, scale: 2 / 3 },
              });
              writeFileSync(`${out}720-${kind}.png`, Buffer.from(data, "base64"));
              await cdp.detach();
            }
            if (width === 1920 && kind === "merged") {
              await page.screenshot({ path: `${out}merging-${theme}.png` });
              await page.clock.runFor(1000);
              await page.screenshot({ path: `${out}halo-${theme}.png` });
            }
          });
        if (width === 1920)
          for (const kind of ["needs", "overlap"])
            await scene(theme, width, height, async (page) => {
              await page.evaluate((kind) => {
                window.milestones.send(kind, true);
              }, kind);
              await page.clock.runFor(kind === "needs" ? 240 : 1100);
              await page.screenshot({
                path: `${out}${kind === "overlap" ? "resting" : "needs"}-overlap-${theme}.png`,
              });
            });
      }
    await scene(
      "dark",
      1920,
      1080,
      async (page) => {
        await page.evaluate(() => {
          window.milestones.send("merged");
        });
        expect(await page.locator("[data-merge-halo]").count()).toBe(0);
        expect(await pill(page, "Merged")).toBe(1);
        await page.screenshot({ path: `${out}reduced-motion.png` });
      },
      true,
    );
    for (const kind of ["ready", "needs", "merged"])
      await scene(
        "dark",
        1920,
        1080,
        async (page) => {
          await page.evaluate((kind) => {
            const { field } = window.milestones.probes();
            for (let i = 0; i < 5; i++) field?.pixel(0, 0);
            window.milestones.send(kind);
          }, kind);
          await page.waitForTimeout(kind === "merged" ? 975 : 200);
          costs[kind] = await page.evaluate(() => {
            const { field } = window.milestones.probes();
            if (!field) throw new Error("No field");
            const gl = document.createElement("canvas").getContext("webgl2"),
              info = gl?.getExtension("WEBGL_debug_renderer_info");
            const times: number[] = [];
            for (let i = 0, end = performance.now() + 250; i < 35 && performance.now() < end; i++) {
              const start = performance.now();
              field.pixel(0, 0);
              times.push(performance.now() - start);
            }
            times.sort((a, b) => a - b);
            return {
              renderer: info && (gl?.getParameter(info.UNMASKED_RENDERER_WEBGL) as string),
              samples: times.length,
              medianMs: times[Math.floor(times.length / 2)],
              p95Ms: times[Math.ceil(times.length * 0.95) - 1],
            };
          });
        },
        false,
        false,
      );
    writeFileSync(`${out}software-gl.json`, JSON.stringify(costs, null, 2));
  },
  240_000,
);
