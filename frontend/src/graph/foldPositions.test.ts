import { expect, it, vi } from "vitest";

import { themes } from "../theme";
import type { GraphElement } from "./elements";
import { foldPositions } from "./foldPositions";

it("measures expanded bounds once per elements snapshot and geometry across fold cycles", () => {
  const elements = modules(2);
  const leaf = elements.find((e) => e.data.id === "m0:f0");
  if (!leaf) throw new Error("Missing measurement sentinel");
  const measure = vi.fn(() => 282);
  Object.defineProperty(leaf.data, "w", { get: measure });
  for (let i = 0; i < 3; i++) {
    foldPositions(elements, new Set(), themes.dark, new Set(["m0"]));
    const open = foldPositions(elements, new Set(), { ...themes.dark });
    for (const e of elements) expect(open.positions.get(e.data.id)).toEqual(e.position);
  }
  expect(measure).toHaveBeenCalledTimes(1);
  foldPositions([...elements], new Set(), themes.dark);
  expect(measure).toHaveBeenCalledTimes(2);
  const theme = { ...themes.dark, "frame-pad-top": "64px" };
  const resized = foldPositions(elements, new Set(), theme);
  expect(resized.positions.get("m0")).not.toEqual(elements[0]?.position);
  foldPositions(elements, new Set(), theme, new Set(["m0"]));
  expect(measure).toHaveBeenCalledTimes(3);
  // Even an in-place theme geometry edit invalidates the cached measurement.
  theme["frame-pad-top"] = "74px";
  foldPositions(elements, new Set(), theme);
  expect(measure).toHaveBeenCalledTimes(4);
});

/** Synthetic expanded modules: two far-apart rows, 80 units between full frames. */
function modules(count: number): GraphElement[] {
  return Array.from({ length: count }, (_, i) => {
    const id = `m${String(i)}`;
    const y = i * 1080;
    return [
      {
        group: "nodes" as const,
        data: { id, kind: "module" as const },
        classes: "module",
        position: { x: 163, y: y + 500 },
      },
      ...[77, 960].map((dy, j) => ({
        group: "nodes" as const,
        data: { id: `${id}:f${String(j)}`, parent: id, kind: "function" as const },
        classes: "function",
        position: { x: 168, y: y + dy },
      })),
    ];
  }).flat();
}

it("subtracts a folded module's full expanded height down to its collapsed header", () => {
  const elements = modules(2);
  const folded = new Set(["m0", "m1"]);
  const result = foldPositions(elements, new Set(), themes.dark, folded);
  expect(result.positions.get("m0")).toEqual({ x: 163, y: 35 });
  expect(result.positions.get("m1")).toEqual({ x: 163, y: 185 });
  expect(result.visible.get("m0:f0")).toBe("m0");
  const partial = foldPositions(elements, new Set(), themes.dark, new Set(["m0"]));
  expect(partial.positions.get("m1:f0")?.y).toBe(227);
});

it("bounds long folded columns and returns every unfolded node to its expanded preset", () => {
  const elements = modules(34);
  const original = structuredClone(elements);
  const ids = elements.filter((e) => e.data.kind === "module").map((e) => e.data.id);
  const project = () => foldPositions(elements, new Set(), themes.dark, new Set(ids));
  const folded = project();
  const columns = new Map<number, number[]>();
  for (const id of ids) {
    const p = folded.positions.get(id);
    if (p) columns.set(p.x, [...(columns.get(p.x) ?? []), p.y]);
  }
  expect(columns.size).toBe(4);
  for (const ys of columns.values()) {
    expect(Math.max(...ys) - Math.min(...ys) + 70).toBeLessThanOrEqual(1350);
    expect(new Set(ys).size).toBe(ys.length);
    for (let i = 1; i < ys.length; i++) expect((ys[i] ?? 0) - (ys[i - 1] ?? 0)).toBe(150);
  }
  const open = foldPositions(elements, new Set(ids), themes.dark);
  for (const e of elements) expect(open.positions.get(e.data.id)).toEqual(e.position);
  expect(project()).toEqual(folded);
  expect(elements).toEqual(original);
});
