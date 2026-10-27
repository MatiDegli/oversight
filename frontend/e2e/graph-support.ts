import type { Page } from "@playwright/test";
import type { GraphProbeHost } from "../src/graph";

/** Node ids by lit state, such as "A read" or "B edit", and how much of each agent's color is drawn. */
interface Sample {
  lit: Record<string, string[]>;
  panels: string[];
  /** For each lit node: the pixels around it in its own agent's token hue, the most seen. */
  paint: Record<string, number>;
  /** The pixels around lit nodes in the other agent's hue, the most seen; none should be. */
  stray: number;
}
type Recorded = Window & { e2eSamples: Sample[] };

/**
 * Runs in the page from its first frame: on every frame, records what is lit and which side panel
 * is open, whenever either changed. A state that lasts a second or two is then never missed
 * because the test was busy with something else, and a panel that opened by itself is on record.
 * It also reads the graph's canvas around each lit node, so the record says what was drawn, in
 * the agent's token color (--agent-a, --agent-b), and not only which class the node carries.
 */
export function installRecorder() {
  const samples: Sample[] = [];
  Object.defineProperty(window, "e2eSamples", { value: samples });
  // The document does not exist yet when this script first runs: read the page's style on a frame.
  const root = () => getComputedStyle(document.documentElement);
  const surface = document.createElement("canvas");
  const pixels = surface.getContext("2d", { willReadFrequently: true });
  if (!pixels) throw new Error("No 2d canvas to read the graph with");
  const hueOf = (r: number, g: number, b: number) => {
    const high = Math.max(r, g, b);
    const chroma = high - Math.min(r, g, b);
    // Only saturated pixels have a hue worth comparing: the borders, not the dark fills and text.
    if (chroma < 60) return -1;
    const sector =
      high === r
        ? ((g - b) / chroma + 6) % 6
        : high === g
          ? (b - r) / chroma + 2
          : (r - g) / chroma + 4;
    return sector * 60;
  };
  const tokenHue = (name: string) => {
    pixels.fillStyle = root().getPropertyValue(name).trim();
    pixels.fillRect(0, 0, 1, 1);
    const [r = 0, g = 0, b = 0] = pixels.getImageData(0, 0, 1, 1).data;
    return hueOf(r, g, b);
  };
  // Read once the graph is up: the stylesheet has not loaded when this script first runs.
  let hues = { A: -1, B: -1 };
  const near = (hue: number, target: number) => {
    const gap = Math.abs(hue - target);
    return Math.min(gap, 360 - gap) <= 14;
  };
  /** How many pixels in the box around (x, y) have each agent's hue, over the graph's canvases. */
  const count = (
    host: HTMLElement,
    x: number,
    y: number,
    halfWidth: number,
    halfHeight: number,
  ) => {
    const left = Math.max(0, Math.round(x - halfWidth));
    const top = Math.max(0, Math.round(y - halfHeight));
    const width = Math.max(1, Math.round(halfWidth * 2));
    const height = Math.max(1, Math.round(halfHeight * 2));
    surface.width = width;
    surface.height = height;
    for (const layer of host.querySelectorAll("canvas")) {
      const scale = layer.width / host.clientWidth;
      pixels.drawImage(
        layer,
        left * scale,
        top * scale,
        width * scale,
        height * scale,
        0,
        0,
        width,
        height,
      );
    }
    const found = { A: 0, B: 0 };
    const { data } = pixels.getImageData(0, 0, width, height);
    for (let at = 0; at < data.length; at += 4) {
      const hue = hueOf(data[at] ?? 0, data[at + 1] ?? 0, data[at + 2] ?? 0);
      if (hue < 0) continue;
      if (near(hue, hues.A)) found.A += 1;
      else if (near(hue, hues.B)) found.B += 1;
    }
    return found;
  };
  let font = 0;
  const row = { width: 0, height: 0 };
  let last = "";
  const sample = () => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const probe = host?.oversightGraph;
    if (host && probe) {
      if (font === 0) {
        font = Number.parseFloat(root().getPropertyValue("--body-size"));
        row.width = Number.parseFloat(root().getPropertyValue("--node-width"));
        row.height = Number.parseFloat(root().getPropertyValue("--node-height"));
        hues = { A: tokenHue("--agent-a"), B: tokenHue("--agent-b") };
        if (!(font > 0) || hues.A < 0 || hues.B < 0 || Math.abs(hues.A - hues.B) < 90)
          throw new Error(
            "The tokens must give a font size and two distinct, saturated agent colors",
          );
      }
      const lit: Record<string, string[]> = {};
      for (const slot of ["A", "B"])
        for (const mode of ["read", "edit"])
          lit[`${slot} ${mode}`] = probe
            .nodeCenters(`node.focus-${slot}-${mode}`)
            .map(({ id }) => id)
            .sort();
      const panels = ["Live diff panel", "Review panel"].filter(
        (label) => document.querySelector(`aside[aria-label="${label}"]`) !== null,
      );
      const key = JSON.stringify({ lit, panels });
      if (key !== last) {
        last = key;
        samples.push({ lit, panels, paint: {}, stray: 0 });
      }
      const current = samples[samples.length - 1];
      if (current) {
        // A row is the theme's node width by node height, 282 by 46; a few pixels more take in its
        // border, where an agent's color is drawn.
        const halfWidth = (row.width / 2) * probe.zoom() + 4;
        const halfHeight = (row.height / 2) * probe.zoom() + 4;
        for (const slot of ["A", "B"] as const)
          for (const mode of ["read", "edit"])
            for (const { id, x, y } of probe.nodeCenters(`node.focus-${slot}-${mode}`)) {
              // The canvas starts at 100% and is panned: a node beyond its edge draws nothing here.
              if (x < 0 || y < 0 || x > host.clientWidth || y > host.clientHeight) continue;
              const found = count(host, x, y, halfWidth, halfHeight);
              current.paint[id] = Math.max(current.paint[id] ?? 0, found[slot]);
              current.stray = Math.max(current.stray, found[slot === "A" ? "B" : "A"]);
            }
      }
    }
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
}

export const recorded = (page: Page) =>
  page.evaluate(() => (window as unknown as Recorded).e2eSamples);

/** The graph's read-only probe: the ids of the nodes a selector matches, sorted. */
export function nodeIds(page: Page, selector: string) {
  return page.evaluate((wanted) => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    if (!probe) throw new Error("The graph is not mounted");
    return probe
      .nodeCenters(wanted)
      .map(({ id }) => id)
      .sort();
  }, selector);
}
