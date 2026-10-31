import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import { look, settledGraph, STRAY, withPage } from "./dotField.browser.support";
import type { Rect } from "./dotField.browser.support";

it("sits under the Cytoscape canvas, takes no pointer and no focus, and shows through it", async () => {
  await withPage({ reducedMotion: "reduce" }, async (page) => {
    const stack = await page.evaluate(() => {
      const { graph, field } = window.fieldTest.elements();
      const wrapper = graph.parentElement;
      const rf = field.getBoundingClientRect();
      const rg = graph.getBoundingClientRect();
      const style = getComputedStyle(field);
      return {
        before: (field.compareDocumentPosition(graph) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
        sameBox:
          [rf.x, rf.y, rf.width, rf.height].join() === [rg.x, rg.y, rg.width, rg.height].join(),
        stacking: [style.position, style.zIndex, getComputedStyle(graph).zIndex],
        pointer: style.pointerEvents,
        hidden: field.getAttribute("aria-hidden"),
        focusable: field.tabIndex,
        cytoscapeCanvases: graph.querySelectorAll("canvas").length,
        atCenter: document.elementFromPoint(rg.x + rg.width / 2, rg.y + rg.height / 2) === field,
        wrapperBackground: wrapper ? getComputedStyle(wrapper).backgroundColor : null,
        graphBackground: getComputedStyle(graph).backgroundColor,
      };
    });
    expect(stack.before).toBe(true);
    expect(stack.sameBox).toBe(true);
    expect(stack.stacking).toEqual(["absolute", "auto", "auto"]);
    expect(stack.pointer).toBe("none");
    expect(stack.hidden).toBe("true");
    expect(stack.focusable).toBe(-1);
    expect(stack.cytoscapeCanvases).toBeGreaterThan(0);
    expect(stack.atCenter).toBe(false);
    // The ground is the wrapper's; the Cytoscape element over the field is transparent.
    expect(stack.wrapperBackground).not.toBe("rgba(0, 0, 0, 0)");
    expect(stack.graphBackground).toBe("rgba(0, 0, 0, 0)");

    // The field shows through the transparent graph canvas: it turns the page warm around the lit node.
    expect(await warmPixelsFromField(page)).toBeGreaterThan(500);
  });
}, 30_000);

/**
 * How many pixels of the graph's area turn warm (agent A's orange) because of the field: the page
 * is shot with the field's canvas shown and with it hidden, and the two are compared.
 */
async function warmPixelsFromField(
  page: Page,
  /** Counts only pixels inside one of `within` and outside every box of `except`, in CSS px. */
  region?: { within: Rect[]; except: Rect[] },
): Promise<number> {
  await settledGraph(page);
  const rect = await page.evaluate(() => {
    const { x, y, width, height } = window.fieldTest.elements().graph.getBoundingClientRect();
    return { x, y, width, height };
  });
  const shown = await page.screenshot({ clip: rect });
  await page.evaluate(() => {
    window.fieldTest.elements().field.style.visibility = "hidden";
  });
  const hidden = await page.screenshot({ clip: rect });
  await page.evaluate(() => {
    window.fieldTest.elements().field.style.visibility = "";
  });
  return page.evaluate(
    async ({ withField, withoutField, only }) => {
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
      const [a, b] = [await decode(withField), await decode(withoutField)];
      const inBox = (list: Rect[], x: number, y: number) =>
        list.some((box) => x >= box.x1 && x <= box.x2 && y >= box.y1 && y <= box.y2);
      let warm = 0;
      for (let at = 0; at < a.length; at += 4) {
        const changed = a[at] !== b[at] || a[at + 1] !== b[at + 1] || a[at + 2] !== b[at + 2];
        // A lamp seen through a translucent fill is a strong tint; a faint one is a stray edge.
        if (!changed || (a[at] ?? 0) <= (a[at + 2] ?? 0) + (only ? 50 : 20)) continue;
        if (only) {
          const x = (at / 4) % only.width;
          const y = Math.floor(at / 4 / only.width);
          if (!inBox(only.within, x, y) || inBox(only.except, x, y)) continue;
        }
        warm += 1;
      }
      return warm;
    },
    {
      withField: shown.toString("base64"),
      withoutField: hidden.toString("base64"),
      only: region ? { ...region, width: Math.round(rect.width) } : null,
    },
  );
}

it("lights lamps near a lit node in its color and leaves the far cells dark", async () => {
  await withPage({ reducedMotion: "reduce" }, async (page) => {
    expect(
      await page.evaluate(() => document.createElement("canvas").getContext("webgl2") !== null),
    ).toBe(true);
    const seen = await look(page);
    expect(seen.state).toBe("webgl");
    expect(seen.draws).toBeGreaterThan(0);
    // The still light at the node is its full weight: at least level 2 of 3, in agent A's orange.
    expect(seen.near?.level).toBeGreaterThanOrEqual(2);
    const [r, , b] = seen.near?.color ?? [0, 0, 0];
    expect(r).toBeGreaterThan(b);
    // The GPU drew the lamp the model computed.
    const lit = seen.near?.color ?? [];
    for (const channel of [0, 1, 2]) {
      expect(Math.abs((seen.gpu?.[channel] ?? -99) - (lit[channel] ?? 99))).toBeLessThanOrEqual(2);
    }
    // Far from every lit node the lamp is off.
    expect(seen.farDistance).toBeGreaterThan(500);
    expect(seen.far?.level).toBe(0);
  });
}, 30_000);

/** The boxes of a graph's frame bodies, and what hides lamps inside them: rows, cards, the masks. */
function frameRegions(page: Page) {
  return page.evaluate(() => {
    const { graph, field } = window.fieldTest.elements();
    const camera = graph.oversightGraph;
    const probe = field.oversightField;
    if (!camera || !probe) throw new Error("No probe");
    const zoom = camera.zoom();
    const pan = camera.pan();
    const place = (b: { x1: number; y1: number; x2: number; y2: number }) => ({
      x1: b.x1 * zoom + pan.x,
      y1: b.y1 * zoom + pan.y,
      x2: b.x2 * zoom + pan.x,
      y2: b.y2 * zoom + pan.y,
    });
    const top = (name: string) =>
      Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)) * zoom;
    const frames = camera.nodeBounds(".module:parent, .class:parent", false).map(place);
    // The text band at the top of each open frame: the padding above its first row, per the theme.
    const bands = [
      ...camera
        .nodeBounds(".module:parent", false)
        .map(place)
        .map((box) => ({ box, band: top("--frame-pad-top") })),
      ...camera
        .nodeBounds(".class:parent", false)
        .map(place)
        .map((box) => ({ box, band: top("--class-pad-top") })),
    ];
    return {
      frames,
      headers: bands.map(({ box, band }) => ({ ...box, y2: Math.min(box.y2, box.y1 + band) })),
      rows: camera.nodeBounds(":childless", false).map(place),
      masks: probe.masks(),
    };
  });
}

it.each([
  ["dark", undefined],
  ["day", "day" as const],
])(
  "shows the %s light through a module's translucent body, never under its header",
  async (_name, theme) => {
    await withPage(
      { scene: "watch", viewport: { width: 1920, height: 1080 }, reducedMotion: "reduce", theme },
      async (page) => {
        const { frames, headers, rows, masks } = await frameRegions(page);
        expect(frames.length).toBeGreaterThan(0);
        expect(masks.length).toBeGreaterThan(0);
        // The cells of a frame's body that no row, folded card or header hides take the lamps through
        // the frame's fill: its panel, translucent. An opaque frame would show none of them.
        const body = await warmPixelsFromField(page, {
          within: frames,
          except: [...rows, ...masks],
        });
        expect(body).toBeGreaterThan(300);
        // Under a header the lamps are masked: the field adds nothing there but a stray few, though
        // the lit row sits right below it.
        const header = await warmPixelsFromField(page, { within: headers, except: rows });
        expect(header).toBeLessThan(STRAY);
      },
    );
  },
  60_000,
);

it("keeps the graph on plain ground where there is no WebGL", async () => {
  await withPage({ noWebGL: true }, async (page) => {
    const seen = await page.evaluate(() => {
      const { graph, field } = window.fieldTest.elements();
      const probe = field.oversightField;
      return {
        state: probe?.state(),
        draws: probe?.draws(),
        pixel: probe?.pixel(10, 10),
        lamp: probe?.lamp(10, 10),
        animating: probe?.animating(),
        nodes: graph.oversightGraph?.nodeCenters().length ?? 0,
      };
    });
    expect(seen).toMatchObject({ state: "plain", draws: 0, pixel: null, lamp: null });
    expect(seen.animating).toBe(false);
    expect(seen.nodes).toBeGreaterThan(0);
    // Away from every node the page is the wrapper's ground, and the field adds nothing warm.
    await settledGraph(page);
    const rect = await page.evaluate(() => {
      const { x, y, width } = window.fieldTest.elements().graph.getBoundingClientRect();
      return { x: x + width - 16, y: y + 12, width: 4, height: 4 };
    });
    const png = await page.screenshot({ clip: rect });
    const ground = await page.evaluate(async (base64) => {
      const bitmap = await createImageBitmap(
        new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))]),
      );
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = canvas.getContext("2d");
      if (!context) throw new Error("No 2d context");
      context.drawImage(bitmap, 0, 0);
      const { graph } = window.fieldTest.elements();
      return {
        pixel: [...context.getImageData(1, 1, 1, 1).data],
        ground: getComputedStyle(graph.parentElement ?? graph).backgroundColor,
      };
    }, png.toString("base64"));
    const [r, g, b] = ground.pixel;
    expect(ground.ground).toBe(`rgb(${String(r)}, ${String(g)}, ${String(b)})`);
    // Without WebGL the ground is the dark ground token, #070707: near black, not pure black.
    expect([r, g, b]).toEqual([7, 7, 7]);
  });
}, 30_000);

it("gives up quietly when a draw fails: the graph stays and the ground is plain", async () => {
  await withPage({ failingDraw: true }, async (page) => {
    const seen = await page.evaluate(() => {
      const { graph, field } = window.fieldTest.elements();
      const probe = field.oversightField;
      // Cytoscape keeps rendering after the field has failed.
      return {
        state: probe?.state(),
        animating: probe?.animating(),
        draws: probe?.draws(),
        nodes: graph.oversightGraph?.nodeCenters().length ?? 0,
      };
    });
    expect(seen).toMatchObject({ state: "plain", animating: false, draws: 0 });
    expect(seen.nodes).toBeGreaterThan(0);
  });
}, 30_000);

it("falls back to plain ground after a real WebGL context loss", async () => {
  await withPage({}, async (page) => {
    await page.evaluate(() => {
      const { field } = window.fieldTest.elements();
      const gl = field.getContext("webgl2");
      const extension = gl?.getExtension("WEBGL_lose_context");
      if (!extension) throw new Error("No context-loss test extension");
      extension.loseContext();
    });
    await browserExpect
      .poll(() =>
        page.evaluate(() => {
          const { field } = window.fieldTest.elements();
          return {
            state: field.oversightField?.state(),
            hidden: field.hidden,
            animating: field.oversightField?.animating(),
            lamp: field.oversightField?.lamp(0, 0),
          };
        }),
      )
      .toEqual({ state: "plain", hidden: true, animating: false, lamp: null });
    expect(
      await page.evaluate(
        () => window.fieldTest.elements().graph.oversightGraph?.nodeCenters().length ?? 0,
      ),
    ).toBeGreaterThan(0);
  });
}, 30_000);

it("moves with breathing and rings, and stops moving under reduced motion", async () => {
  // With motion, frames run and what the lamps show changes: a ring spreads from the node.
  await withPage({ reducedMotion: "no-preference" }, async (page) => {
    const sample = () =>
      page.evaluate(
        ({ changed }) => {
          const { graph, field } = window.fieldTest.elements();
          const probe = field.oversightField;
          const center = graph.oversightGraph?.nodeCenters().find(({ id }) => id === changed);
          if (!probe || !center) throw new Error("The field or its lit node is missing");
          // A line of cells out from the node, to the right: the ring crosses it.
          const line: string[] = [];
          for (let x = 0; x < 340; x += 7) {
            const lamp = probe.lamp(center.x + x, center.y);
            line.push(lamp ? [lamp.level, ...lamp.color].join(",") : "-");
          }
          return { animating: probe.animating(), draws: probe.draws(), line: line.join(" ") };
        },
        { changed: "shop.pricing:compute_price" },
      );
    const first = await sample();
    expect(first.animating).toBe(true);
    await browserExpect
      .poll(async () => (await sample()).draws, { timeout: 15_000 })
      .toBeGreaterThan(first.draws + 3);
    await browserExpect
      .poll(async () => (await sample()).line, { timeout: 15_000 })
      .not.toBe(first.line);
    // A tab that is hidden stops the frames, and showing it again starts them.
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect((await sample()).animating).toBe(false);
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect((await sample()).animating).toBe(true);
  });

  // Under reduced motion no frame is scheduled, and the light is the still light.
  await withPage({ reducedMotion: "reduce" }, async (page) => {
    const sample = () =>
      page.evaluate(
        ({ changed }) => {
          const { graph, field } = window.fieldTest.elements();
          const probe = field.oversightField;
          const center = graph.oversightGraph?.nodeCenters().find(({ id }) => id === changed);
          if (!probe || !center) throw new Error("The field or its lit node is missing");
          // The same cells as above, computed afresh by a redraw at the current clock.
          probe.pixel(0, 0);
          const line: string[] = [];
          for (let x = 0; x < 340; x += 7) {
            const lamp = probe.lamp(center.x + x, center.y);
            line.push(lamp ? [lamp.level, ...lamp.color].join(",") : "-");
          }
          return { animating: probe.animating(), line: line.join(" ") };
        },
        { changed: "shop.pricing:compute_price" },
      );
    await settledGraph(page);
    const first = await sample();
    expect(first.animating).toBe(false);
    // Frames go by, and the clock with them; the lamps stay the still light.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          let frames = 0;
          const count = () => {
            frames += 1;
            if (frames >= 20) resolve();
            else requestAnimationFrame(count);
          };
          requestAnimationFrame(count);
        }),
    );
    expect(await sample()).toEqual(first);
  });
}, 30_000);

it.each(["review", "live-diff"] as const)(
  "draws the %s light on its change and cone",
  async (scene) => {
    // The live diff opens on an agent that is editing: the working light, at its strongest. Review
    // opens on a change that waits on an open inbox entry: the steady, dimmer light.
    await withPage({ reducedMotion: "reduce", scene }, async (page) => {
      const seen = await look(page);
      expect(seen.state).toBe("webgl");
      expect(seen.near?.level).toBeGreaterThanOrEqual(scene === "review" ? 1 : 2);
      const graph = await page.evaluate(() => {
        const probe = window.fieldTest.elements().graph.oversightGraph;
        return {
          cone: probe?.nodeCenters("node.cone:not(.dimmed)").length ?? 0,
          dimmed: probe?.nodeCenters("node.dimmed").length ?? 0,
          zoom: probe?.zoom() ?? 0,
          labelSizes: ["--node-name-size", "--node-word-size"].map((token) =>
            Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(token)),
          ),
        };
      });
      expect(graph.cone).toBeGreaterThan(0);
      expect(graph.dimmed).toBeGreaterThan(0);
      expect(Math.min(...graph.labelSizes) * graph.zoom).toBeGreaterThanOrEqual(11);
      expect(graph.zoom).toBeLessThanOrEqual(1);
    });
  },
  30_000,
);
