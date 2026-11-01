import { describe, expect, it } from "vitest";
import { themes } from "../theme";
import type { GraphElement } from "./elements";
import { foldPositions } from "./foldPositions";
import { groupPack } from "./groupPack";
import type { GroupBox } from "./groupPack";

function input(counts: Record<string, number>) {
  const roots: GraphElement[] = [],
    full = new Map<string, GroupBox>(),
    heights = new Map<string, number>();
  for (const [group, count] of Object.entries(counts))
    for (let i = 0; i < count; i += 1) {
      const id = `${group}-${String(i).padStart(2, "0")}`;
      roots.push({
        group: "nodes",
        classes: "module",
        data: { id, kind: "module", group, w: 326, h: 70 },
        position: { x: Math.floor(i / 9) * 446 + 163, y: (i % 9) * 220 + 35 },
      });
      full.set(id, { x: Math.floor(i / 9) * 446, y: (i % 9) * 220, width: 326, height: 70 });
      heights.set(id, 70);
    }
  return { roots, full, heights };
}
function pack(counts: Record<string, number>) {
  const { roots, full, heights } = input(counts);
  const result = groupPack(roots, full, heights, themes.dark);
  if (!result) throw new Error("Expected groups");
  return result;
}

describe("group packing", () => {
  it("uses 446 by 220 cells, nine to a column, with card tops at cell tops", () => {
    const { positions, cellWidth, cellHeight } = pack({ lib: 10, misc: 2 });
    expect([cellWidth, cellHeight]).toEqual([446, 220]);
    expect(positions.get("lib-00")).toEqual({ x: 223, y: 35 });
    expect(positions.get("lib-08")).toEqual({ x: 223, y: 1795 });
    expect(positions.get("lib-09")).toEqual({ x: 669, y: 35 });
  });
  it("leaves 340 sideways and 460 stacked, sharing the first row's top", () => {
    const { boxes } = pack({ lib: 30, misc: 3, docs: 1 });
    expect(boxes.get("lib")).toEqual({ x: 0, y: 0, width: 1784, height: 1980 });
    expect(boxes.get("misc")).toEqual({ x: 2124, y: 0, width: 446, height: 660 });
    expect(boxes.get("docs")).toEqual({ x: 2124, y: 1120, width: 446, height: 220 });
  });
  it("sorts groups by count then label, with tests last", () => {
    expect([...pack({ tests: 32, zebra: 3, alpha: 3, lib: 30 }).boxes.keys()]).toEqual([
      "lib",
      "alpha",
      "zebra",
      "tests",
    ]);
  });
  it("is deterministic across input permutations, ordering modules by box x, y, then id", () => {
    const { roots, full, heights } = input({ lib: 20, docs: 2 });
    const first = groupPack(roots, full, heights, themes.dark);
    expect(groupPack([...roots].reverse(), full, heights, themes.dark)).toEqual(first);
    expect(groupPack(roots, full, heights, themes.dark)).toEqual(first);
    full.set("lib-01", { x: -1, y: 999, width: 326, height: 70 });
    expect(groupPack(roots, full, heights, themes.dark)?.positions.get("lib-01")).toEqual({
      x: 223,
      y: 35,
    });
  });
  it("grows an open card's cell and shifts the cells below", () => {
    const { roots, full, heights } = input({ lib: 10, docs: 2 });
    heights.set("lib-00", 570);
    const packed = groupPack(roots, full, heights, themes.dark);
    expect(packed?.positions.get("lib-00")).toEqual({ x: 223, y: 285 });
    expect(packed?.positions.get("lib-01")).toEqual({ x: 223, y: 755 });
    expect(packed?.positions.get("lib-09")).toEqual({ x: 669, y: 35 });
  });
  it("does not change the existing fold projection with fewer than two groups", () => {
    const { roots, full, heights } = input({ shop: 4 });
    expect(groupPack(roots, full, heights, themes.dark)).toBeNull();
    const plain = roots.map((r) => ({ ...r, data: { ...r.data, group: undefined } }));
    for (const expanded of [new Set<string>(), new Set(roots.map((r) => r.data.id))]) {
      const folded = new Set(roots.filter((r) => !expanded.has(r.data.id)).map((r) => r.data.id));
      expect(foldPositions(roots, expanded, themes.dark, folded)).toEqual(
        foldPositions(plain, expanded, themes.dark, folded),
      );
    }
  });
  it("applies the grouped projection at every depth", () => {
    const { roots } = input({ lib: 10, docs: 2 });
    for (const expanded of [new Set<string>(), new Set(roots.map((r) => r.data.id))]) {
      const folded = new Set(roots.filter((r) => !expanded.has(r.data.id)).map((r) => r.data.id));
      expect(foldPositions(roots, expanded, themes.dark, folded).positions.get("lib-09")).toEqual({
        x: 669,
        y: 35,
      });
    }
  });
});
