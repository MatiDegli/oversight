import cytoscape from "cytoscape";
import type { Core, StylesheetJson } from "cytoscape";
import { afterEach, describe, expect, it, vi } from "vitest";

import { themes } from "../theme";

import { imageSource } from "./layerImages";
import { graphStyles } from "./styles";

const graphs: Core[] = [];
const elements = ["a", "b"].map((id) => ({
  data: { id, title: "same.py", count: "1 function", w: 326, h: 70 },
  classes: "module folded",
}));
function create(style: StylesheetJson = graphStyles(themes.dark)) {
  const cy = cytoscape({ headless: true, styleEnabled: true, style, elements });
  graphs.push(cy);
  return cy;
}
const images = (cy: Core, id = "a") =>
  String(cy.getElementById(id).style("background-image")).split(" ");
afterEach(() => {
  for (const cy of graphs.splice(0)) cy.destroy();
  vi.restoreAllMocks();
});

describe("short layer image URLs", () => {
  it("shares distinct SVGs and preserves their bytes and embedded fonts", async () => {
    const allocated = vi.spyOn(URL, "createObjectURL");
    const cy = create();
    const urls = images(cy);
    expect(images(cy, "b")).toEqual(urls);
    expect(urls).toHaveLength(2);
    expect(allocated).toHaveBeenCalledTimes(2);
    for (const [index, url] of urls.entries()) {
      expect(url.startsWith("blob:")).toBe(true);
      expect(url.length).toBeLessThan(100);
      const blob = allocated.mock.calls[index]?.[0];
      if (!(blob instanceof Blob)) throw new Error("Missing SVG blob");
      expect(blob.type).toBe("image/svg+xml");
      const svg = await blob.text();
      expect(svg).toBe(decodeURIComponent(imageSource(url).slice("data:image/svg+xml,".length)));
      expect(svg).toContain("@font-face");
    }
  });

  it("revokes an old style set before allocating its replacement", () => {
    const revoked = vi.spyOn(URL, "revokeObjectURL");
    const cy = create();
    const before = images(cy);
    const sources = before.map(imageSource);
    cy.style(graphStyles(themes.dark));
    expect(revoked.mock.calls.map(([url]) => url)).toEqual(before);
    const after = images(cy);
    expect(after).not.toEqual(before);
    expect(after.map(imageSource)).toEqual(sources);
    for (const url of before) expect(imageSource(url)).toBe(url);
    revoked.mockClear();
    cy.destroy();
    expect(revoked.mock.calls.map(([url]) => url)).toEqual(after);
  });

  it("also releases when the replacement stylesheet has no layer callbacks", () => {
    const revoked = vi.spyOn(URL, "revokeObjectURL");
    const cy = create();
    const before = images(cy);
    cy.style([{ selector: "node", style: { "background-image": "none" } }]);
    expect(revoked.mock.calls.map(([url]) => url)).toEqual(before);
    expect(images(cy)).toEqual(["none"]);
    for (const url of before) expect(imageSource(url)).toBe(url);
  });

  it("releases an empty graph's images and can allocate again", () => {
    const revoked = vi.spyOn(URL, "revokeObjectURL");
    const cy = create();
    const before = images(cy);
    cy.elements().remove();
    expect(revoked.mock.calls.map(([url]) => url)).toEqual(before);
    cy.style(graphStyles(themes.dark));
    cy.add(elements);
    expect(images(cy)).not.toEqual(before);
  });

  it("keeps a shared style's cores independent when one is destroyed", () => {
    const revoked = vi.spyOn(URL, "revokeObjectURL");
    const style = graphStyles(themes.dark);
    const first = create(style);
    const second = create(style);
    const a = images(first);
    const b = images(second);
    expect(a).not.toEqual(b);
    first.destroy();
    expect(revoked.mock.calls.map(([url]) => url)).toEqual(a);
    for (const url of b) expect(imageSource(url)).toContain("data:image/svg+xml,");
  });

  it("allocates and revokes nothing on an equal write or restyle", () => {
    const allocated = vi.spyOn(URL, "createObjectURL");
    const revoked = vi.spyOn(URL, "revokeObjectURL");
    const cy = create();
    const before = images(cy);
    allocated.mockClear();
    cy.getElementById("a").data("title", "same.py");
    expect(images(cy)).toEqual(before);
    expect(allocated).not.toHaveBeenCalled();
    expect(revoked).not.toHaveBeenCalled();
  });
});
