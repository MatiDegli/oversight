import { describe, expect, it } from "vitest";

import { themes } from "../theme";
import { headerLayers, marksClearance, marksLayer } from "./layers";
import type { NodeMarks } from "./layers";

const theme = themes.dark;
const leaf = { width: 282, height: 46 };
const module = { width: 326, height: 70 };
const narrow = { width: 214, height: 70 };
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
    expect(svg).toContain(`rx="${theme["radius-node"]}" fill="${theme["agent-a"]}"/>`);
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
    // A badge is 26px wide; the last one's right edge is the right padding less half a border
    // from the node's outer edge.
    const half = Number.parseFloat(theme["letter-badge-size"]) / 2;
    const right = Number.parseFloat(theme["node-pad-right"]) - 0.5;
    expect(xs).toHaveLength(2);
    expect((xs[1] ?? 0) + half).toBe(leaf.width - right);
    expect((xs[1] ?? 0) - (xs[0] ?? 0)).toBe(2 * half + Number.parseFloat(theme["spacing-xs"]));
  });

  it("says the word on the badges' line, right-aligned a gap left of them", () => {
    const svg = drawn(marksLayer(theme, changed, leaf).image);
    const word = /<text x="([\d.]+)" y="([\d.]+)"[^>]*text-anchor="end"[^>]*>changed<\/text>/.exec(
      svg,
    );
    const right = leaf.width - (Number.parseFloat(theme["node-pad-right"]) - 0.5);
    expect(Number(word?.[1])).toBe(right - 26 - Number.parseFloat(theme["node-word-gap"]));
    expect(Number(word?.[2])).toBe(leaf.height / 2);
    // The name is told how much of the row the marks take, the word and the gap and the badges.
    expect(marksClearance(theme, changed)).toBe(
      "changed".length * 16 * 0.55 + Number.parseFloat(theme["node-word-gap"]) + 26,
    );
  });

  it("covers the leaf's own box and uses only the theme's colors", () => {
    const layer = marksLayer(theme, changed, leaf);
    expect([layer.width, layer.height, layer.x, layer.y]).toEqual([282, 46, "0%", "0%"]);
    const colors = new Set<string>(Object.values(theme).filter((v) => typeof v === "string"));
    for (const color of drawn(layer.image).match(/#[0-9a-f]{6}/gi) ?? [])
      expect(colors.has(color.toLowerCase())).toBe(true);
  });
});

describe("headerLayers", () => {
  it("draws the title at the left and the count at the right, in their own colors", () => {
    const [title, count] = headerLayers(theme, "pricing.py", "6 functions", module, "module");
    expect(drawn(title?.image ?? "")).toContain(
      `fill="${theme["module-label"]}">pricing.py</text>`,
    );
    expect(drawn(count?.image ?? "")).toContain(`fill="${theme.faint}">6 functions</text>`);
    expect(drawn(count?.image ?? "")).toContain('text-anchor="end"');
    expect([title?.x, count?.x]).toEqual(["0%", "100%"]);
    // The title starts the sketch's inset in from the frame's edge, less half the border the
    // image starts inside; the count keeps the same inset at the right.
    expect(title?.offsetX).toBe(22.5);
    expect(count?.offsetX).toBe(-23);
  });

  it("escapes a title that has markup characters in it", () => {
    const [title] = headerLayers(theme, "a<b>&c", "1 function", module, "module");
    expect(drawn(title?.image ?? "")).toContain(">a&lt;b&gt;&amp;c</text>");
  });

  it("centres the header row on the module's band, the class's, or the middle of an empty frame", () => {
    const at = (band: "module" | "class" | "empty") => {
      const [title, count] = headerLayers(theme, "x", "1 function", module, band);
      return [(title?.offsetY ?? 0) + 14, (count?.offsetY ?? 0) + 14];
    };
    expect(at("module")).toEqual([29, 29]);
    expect(at("class")).toEqual([22, 22]);
    expect(at("empty")).toEqual([35, 35]);
  });
});

describe("headerLayers for a frame an agent touches", () => {
  it("puts the state word and the badges in the count's place, right-aligned", () => {
    const layers = headerLayers(theme, "pricing.py", "6 functions", module, "module", changed);
    expect(layers).toHaveLength(2);
    const cluster = drawn(layers[1]?.image ?? "");
    expect(cluster).toContain(`fill="${theme["agent-a-text"]}">changed</text>`);
    expect(cluster).toContain('text-anchor="end"');
    expect(cluster).toContain(`rx="${theme["radius-node"]}" fill="${theme["agent-a"]}"/>`);
    expect(cluster).toContain(`fill="${theme["on-agent"]}">A</text>`);
    // The count gives way to the state; the title keeps its own look.
    expect(cluster).not.toContain("6 functions");
    expect(drawn(layers[0]?.image ?? "")).toContain(
      `fill="${theme["module-label"]}">pricing.py</text>`,
    );
    expect([layers[1]?.x, layers[1]?.offsetX]).toEqual(["100%", -23]);
  });

  it("leaves the title only the room the cluster leaves in the frame, never overlapping it", () => {
    const tight = headerLayers(theme, "pricing.py", "", narrow, "module", changed);
    const wide = headerLayers(
      theme,
      "pricing.py",
      "",
      { width: 674, height: 70 },
      "module",
      changed,
    );
    const cluster = tight[1]?.width ?? 0;
    // The frame's width, less both insets, the cluster and the gap between.
    expect(tight[0]?.width).toBe(214 - 2 * 23 - cluster - 10);
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
    const two = headerLayers(theme, "pricing.py", "", narrow, "module", both);
    expect((two[1]?.width ?? 0) - cluster).toBe(26 + 6);
    expect(two[0]?.width).toBe((tight[0]?.width ?? 0) - 32);
    // A frame too narrow for the cluster draws no title at all, not an overlap.
    expect(
      headerLayers(theme, "x", "", { width: 60, height: 70 }, "module", changed)[0]?.width,
    ).toBe(0);
  });
});

describe("headerLayers for a title the cluster leaves too little room", () => {
  it("cuts it with an ellipsis rather than in the middle of a letter", () => {
    const [title] = headerLayers(theme, "PremiumCustomer", "", narrow, "module", changed);
    const text = />([^<]*)<\/text>/.exec(drawn(title?.image ?? ""))?.[1] ?? "";
    expect(text.endsWith("\u2026")).toBe(true);
    expect(text.length).toBeLessThan("PremiumCustomer".length);
    expect("PremiumCustomer".startsWith(text.slice(0, -1))).toBe(true);
  });

  it("keeps a title that fits whole, and draws nothing where there is no room", () => {
    const [fits] = headerLayers(theme, "pricing.py", "", module, "module", changed);
    expect(drawn(fits?.image ?? "")).toContain(">pricing.py</text>");
    const [none] = headerLayers(
      theme,
      "pricing.py",
      "",
      { width: 60, height: 70 },
      "module",
      changed,
    );
    expect(drawn(none?.image ?? "")).not.toContain("pricing");
  });
});

describe("headerLayers for an empty frame the size of a header", () => {
  it("shows the title and then the count on one line", () => {
    const [title, count] = headerLayers(theme, "__init__.py", "0 functions", module, "empty");
    expect(drawn(title?.image ?? "")).toContain(
      `fill="${theme["module-label"]}">__init__.py</text>`,
    );
    expect(drawn(count?.image ?? "")).toContain(`fill="${theme.faint}">0 functions</text>`);
    expect((title?.offsetY ?? 0) + (title?.height ?? 0) / 2).toBe(module.height / 2);
  });

  it("shows the state word and badges instead of the count when an agent touches it", () => {
    const layers = headerLayers(theme, "__init__.py", "0 functions", module, "empty", read);
    expect(layers).toHaveLength(2);
    const second = drawn(layers[1]?.image ?? "");
    expect(second).toContain(`fill="${theme["agent-b-text"]}">read</text>`);
    expect(second).toContain(`fill="${theme["agent-b-text"]}">B</text>`);
    expect(second).not.toContain("0 functions");
  });
});
