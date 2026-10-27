import { describe, expect, it } from "vitest";

import { themes } from "../theme";
import { headerLayers, marksLayer, stackedHeaderLayers } from "./layers";
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
    const [title, count] = headerLayers(theme, "pricing.py", "6 functions · 1 new", 214);
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
    const [title] = headerLayers(theme, "a<b>&c", "1 function", 214);
    expect(drawn(title?.image ?? "")).toContain(">a&lt;b&gt;&amp;c</text>");
  });
});

describe("headerLayers for a frame an agent touches", () => {
  it("puts the state word and the badges in the count's place, right-aligned", () => {
    const layers = headerLayers(theme, "pricing.py", "6 functions", 214, changed);
    expect(layers).toHaveLength(2);
    const cluster = drawn(layers[1]?.image ?? "");
    expect(cluster).toContain(`fill="${theme["agent-a-text"]}">changed</text>`);
    expect(cluster).toContain('text-anchor="end"');
    expect(cluster).toContain(`rx="${theme["radius-label"]}" fill="${theme["agent-a"]}"/>`);
    expect(cluster).toContain(`fill="${theme["on-agent"]}">A</text>`);
    // The count gives way to the state; the title keeps its own look.
    expect(cluster).not.toContain("6 functions");
    expect(drawn(layers[0]?.image ?? "")).toContain(
      `fill="${theme["module-label"]}">pricing.py</text>`,
    );
    expect([layers[1]?.x, layers[1]?.offsetX]).toEqual(["100%", -14]);
  });

  it("leaves the title only the room the cluster leaves in the frame, never overlapping it", () => {
    const narrow = headerLayers(theme, "pricing.py", "", 214, changed);
    const wide = headerLayers(theme, "pricing.py", "", 674, changed);
    const cluster = narrow[1]?.width ?? 0;
    // The frame's width, less both paddings, the cluster and the gap between.
    expect(narrow[0]?.width).toBe(214 - 2 * 14 - cluster - 6);
    expect(wide[0]?.width).toBe(240);
    // Two badges make a wider cluster and so a narrower title.
    const both: NodeMarks = {
      word: "editing",
      slot: "A",
      badges: [
        { slot: "A", filled: true },
        { slot: "B", filled: false },
      ],
    };
    const two = headerLayers(theme, "pricing.py", "", 214, both);
    expect((two[1]?.width ?? 0) - cluster).toBe(20 + 6);
    expect(two[0]?.width).toBe((narrow[0]?.width ?? 0) - 26);
    // A frame too narrow for the cluster draws no title at all, not an overlap.
    expect(headerLayers(theme, "x", "", 60, changed)[0]?.width).toBe(0);
  });
});

describe("headerLayers for a title the cluster leaves too little room", () => {
  it("cuts it with an ellipsis rather than in the middle of a letter", () => {
    const [title] = headerLayers(theme, "PremiumCustomer", "", 214, changed);
    const text = />([^<]*)<\/text>/.exec(drawn(title?.image ?? ""))?.[1] ?? "";
    expect(text.endsWith("\u2026")).toBe(true);
    expect(text.length).toBeLessThan("PremiumCustomer".length);
    expect("PremiumCustomer".startsWith(text.slice(0, -1))).toBe(true);
  });

  it("keeps a title that fits whole, and draws nothing where there is no room", () => {
    const [fits] = headerLayers(theme, "pricing.py", "", 214, changed);
    expect(drawn(fits?.image ?? "")).toContain(">pricing.py</text>");
    const [none] = headerLayers(theme, "pricing.py", "", 60, changed);
    expect(drawn(none?.image ?? "")).not.toContain("pricing");
  });
});

describe("stackedHeaderLayers", () => {
  it("shows the title and then the count, for an empty frame the size of a leaf", () => {
    const [title, count] = stackedHeaderLayers(theme, "__init__.py", "0 functions", leaf);
    expect(drawn(title?.image ?? "")).toContain(
      `fill="${theme["module-label"]}">__init__.py</text>`,
    );
    expect(drawn(count?.image ?? "")).toContain(`fill="${theme.faint}">0 functions</text>`);
    // A leaf's padding, not a frame's, and the title never wider than the box less it.
    expect(title?.offsetX).toBe(10);
    expect(title?.width).toBe(leaf.width - 20);
    // The count sits on the row a leaf's state word takes: its centre is at 60 - 10 - 10.
    expect((count?.offsetY ?? 0) + (count?.height ?? 0) / 2).toBe(40);
  });

  it("shows the state word and badges instead of the count when an agent touches it", () => {
    const layers = stackedHeaderLayers(theme, "__init__.py", "0 functions", leaf, read);
    expect(layers).toHaveLength(2);
    const second = drawn(layers[1]?.image ?? "");
    expect(second).toContain(`fill="${theme["agent-b-text"]}">read</text>`);
    expect(second).toContain(`fill="${theme["agent-b-text"]}">B</text>`);
    expect(second).not.toContain("0 functions");
  });
});
