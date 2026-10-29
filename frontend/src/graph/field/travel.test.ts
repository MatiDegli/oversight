import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import { afterlightOf } from "./afterlight";
import { approachPath, pathLength, slicePath, trailPieces } from "./travel";

const L = [
  { x: 0, y: 0 },
  { x: 100, y: 0 },
  { x: 100, y: 50 },
];

describe("a path's length and slices", () => {
  it("measures a path along its corners", () => {
    expect(pathLength(L)).toBe(150);
    expect(pathLength([])).toBe(0);
    expect(pathLength([{ x: 3, y: 4 }])).toBe(0);
  });

  it("slices between two arc lengths, keeping the corner it passes", () => {
    expect(slicePath(L, 50, 120)).toEqual([
      { x: 50, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 20 },
    ]);
    expect(slicePath(L, 10, 40)).toEqual([
      { x: 10, y: 0 },
      { x: 40, y: 0 },
    ]);
  });

  it("clamps to the path, and is empty when the slice has no length", () => {
    expect(slicePath(L, -50, 30)).toEqual([
      { x: 0, y: 0 },
      { x: 30, y: 0 },
    ]);
    expect(slicePath(L, 140, 400).at(-1)).toEqual({ x: 100, y: 50 });
    expect(slicePath(L, 60, 60)).toEqual([]);
    expect(slicePath(L, 200, 300)).toEqual([]);
    expect(slicePath([], 0, 10)).toEqual([]);
  });
});

describe("the way a pulse takes to its node", () => {
  const target = { x1: 600, y1: 300, x2: 800, y2: 340 };
  const start = { x: 1000, y: 120 };

  it("runs level to the far end of the nearest edge into the node, then along it", () => {
    const near = [
      { x: 900, y: 100 },
      { x: 900, y: 320 },
      { x: 800, y: 320 },
    ];
    const far = [
      { x: 100, y: 500 },
      { x: 600, y: 500 },
      { x: 600, y: 340 },
    ];
    const path = approachPath(start, target, [far, near]);
    expect(path).toEqual([start, { x: 900, y: 120 }, ...near]);
    expect(approachPath(start, target, [near, far])).toEqual(path);
  });

  it("ends at the node whatever it follows", () => {
    const route = [
      { x: 900, y: 100 },
      { x: 800, y: 320 },
    ];
    expect(approachPath(start, target, [route]).at(-1)).toEqual({ x: 800, y: 320 });
  });

  it("reaches a node with no edge by level and vertical runs to its right side", () => {
    expect(approachPath(start, target, [])).toEqual([
      start,
      { x: 900, y: 120 },
      { x: 900, y: 320 },
      { x: 800, y: 320 },
    ]);
    expect(approachPath(start, target, [[{ x: 1, y: 1 }]])).toEqual(
      approachPath(start, target, []),
    );
  });
});

describe("the trail behind a head", () => {
  const look = { bright: 50, faint: 150 };
  const line = [
    { x: 0, y: 0 },
    { x: 1000, y: 0 },
  ];

  it("fades from full at the head to a little over half, then out over the faint stretch", () => {
    const pieces = trailPieces(line, 500, look, 10);
    expect(pieces).toHaveLength(10);
    const opacities = pieces.map((piece) => piece.opacity);
    expect(opacities).toEqual([...opacities].sort((a, b) => b - a));
    expect(opacities[0]).toBeGreaterThan(0.9);
    expect(opacities.at(-1)).toBeLessThan(0.1);
    expect(opacities.every((value) => value > 0 && value <= 1)).toBe(true);
    // The pieces tile the trail from the head back, 200px in all.
    expect(pieces[0]?.points.at(-1)).toEqual({ x: 500, y: 0 });
    expect(pieces.at(-1)?.points[0]).toEqual({ x: 300, y: 0 });
  });

  it("is short with the theme's own tokens: about 100 px behind the head, gone at its end", () => {
    const own = afterlightOf(themes.dark);
    const pieces = trailPieces(line, 500, own, 12);
    const reach = (pieces[0]?.points.at(-1)?.x ?? 0) - (pieces.at(-1)?.points[0]?.x ?? 0);
    expect(reach).toBeLessThanOrEqual(100);
    expect(pieces.at(-1)?.opacity).toBeLessThan(0.1);
    // Past a quarter of the way back the trail is under half as bright as at the head.
    expect(pieces[Math.floor(pieces.length / 2)]?.opacity).toBeLessThan(0.5);
  });

  it("is cut where the path starts, so a head near the card has a short trail", () => {
    const pieces = trailPieces(line, 60, look, 10);
    expect(pieces.length).toBeGreaterThan(0);
    expect(pieces.length).toBeLessThan(10);
    expect(pieces.every((piece) => piece.points.every((p) => p.x >= 0))).toBe(true);
    expect(trailPieces(line, 0, look, 10)).toEqual([]);
  });
});
