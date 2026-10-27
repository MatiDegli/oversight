import { describe, expect, it } from "vitest";

import { themes } from "../theme";
import { headerLayers, marksLayer } from "./layers";
import type { NodeMarks } from "./layers";

const theme = themes.dark;
const leaf = { width: 150, height: 60 };
const svgOf = (image: string) => decodeURIComponent(image.slice("data:image/svg+xml,".length));
/** The SVG without the embedded fonts, whose base64 would drown the assertions. */
const drawn = (image: string) => svgOf(image).replace(/<style>.*?<\/style>/, "");

const changed: NodeMarks = { word: "changed", slot: "A", badges: [{ slot: "A", filled: true }] };
const read: NodeMarks = { word: "read", slot: "B", badges: [{ slot: "B", filled: false }] };

describe("marksLayer", () => {
  it("says the word in the agent's text color and fills the badge for a change", () => {
    const svg = drawn(marksLayer(theme, changed, leaf).image);
    expect(svg).toContain(`fill="${theme["agent-a-text"]}">changed</text>`);
    // A filled square in the agent's color, its letter in on-agent.
    expect(svg).toContain(`rx="${theme["radius-label"]}" fill="${theme["agent-a"]}"/>`);
    expect(svg).toContain(`fill="${theme["on-agent"]}">A</text>`);
  });

  it("outlines the badge for a node the agent only read, with the letter in the agent's color", () => {
    const svg = drawn(marksLayer(theme, read, leaf).image);
    expect(svg).toContain(`fill="none" stroke="${theme["agent-b"]}"`);
    expect(svg).toContain(`fill="${theme["agent-b-text"]}">B</text>`);
    expect(svg).toContain(`fill="${theme["agent-b-text"]}">read</text>`);
    expect(svg).not.toContain(theme["on-agent"]);
  });

  it("puts one badge per agent, A before B, the last at the right padding", () => {
    const both: NodeMarks = {
      word: "editing",
      slot: "B",
      badges: [
        { slot: "A", filled: false },
        { slot: "B", filled: true },
      ],
    };
    const svg = drawn(marksLayer(theme, both, leaf).image);
    expect(svg.indexOf(">A</text>")).toBeLessThan(svg.indexOf(">B</text>"));
    const xs = [...svg.matchAll(/<text x="([\d.]+)" [^>]*text-anchor="middle"/g)].map((m) =>
      Number(m[1]),
    );
    // Each badge is 20px wide; the last one's right edge is 10px from the node's.
    expect(xs).toHaveLength(2);
    expect((xs[1] ?? 0) + 10).toBe(leaf.width - 10);
    expect((xs[1] ?? 0) - (xs[0] ?? 0)).toBe(20 + 6);
  });

  it("covers the leaf's own box and uses only the theme's colors", () => {
    const layer = marksLayer(theme, changed, leaf);
    expect([layer.width, layer.height, layer.x, layer.y]).toEqual([150, 60, "0%", "0%"]);
    const colors = new Set<string>(Object.values(theme).filter((v) => typeof v === "string"));
    for (const color of drawn(layer.image).match(/#[0-9a-f]{6}/gi) ?? [])
      expect(colors.has(color.toLowerCase())).toBe(true);
  });
});

describe("headerLayers", () => {
  it("draws the title at the left and the count at the right, in their own colors", () => {
    const [title, count] = headerLayers(theme, "pricing.py", "6 functions · 1 new");
    expect(drawn(title?.image ?? "")).toContain(
      `fill="${theme["module-label"]}">pricing.py</text>`,
    );
    expect(drawn(count?.image ?? "")).toContain(`fill="${theme.faint}">6 functions · 1 new</text>`);
    expect(drawn(count?.image ?? "")).toContain('text-anchor="end"');
    expect([title?.x, count?.x]).toEqual(["0%", "100%"]);
    // The count's offset is negative: it keeps the frame's right padding.
    expect(title?.offsetX).toBe(14);
    expect(count?.offsetX).toBe(-14);
  });

  it("escapes a title that has markup characters in it", () => {
    const [title] = headerLayers(theme, "a<b>&c", "1 function");
    expect(drawn(title?.image ?? "")).toContain(">a&lt;b&gt;&amp;c</text>");
  });
});
