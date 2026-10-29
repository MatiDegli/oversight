import { createHash } from "node:crypto";

import { expect, it } from "vitest";

import { themes } from "../theme";

import { routeEdges, tuningFrom } from "./routes";
import type { RouteBox, RouteEdge } from "./routes";

/** Own synthetic fixture: stacked frames, back calls, shared trunks and blocked endpoints. */
function fixture() {
  const boxes: RouteBox[] = [];
  for (let module = 0; module < 8; module++) {
    const id = `m${String(module)}`;
    const x1 = (module % 4) * 470;
    const y1 = Math.floor(module / 4) * 600;
    boxes.push({ id, top: id, x1, y1, x2: x1 + 326, y2: y1 + 445, anchorY: y1 + 29 });
    for (let row = 0; row < 6; row++) {
      const y = y1 + 77 + row * 57;
      boxes.push({
        id: `${id}:f${String(row)}`,
        top: id,
        x1: x1 + 27,
        x2: x1 + 309,
        y1: y - 23,
        y2: y + 23,
        anchorY: y,
      });
    }
  }
  const rows = boxes.filter((box) => box.id !== box.top);
  const edges: RouteEdge[] = Array.from({ length: 168 }, (_, i) => ({
    id: `e${String(i)}`,
    source: rows[i % rows.length]?.id ?? "missing",
    target: rows[(i * 13 + Math.floor(i / rows.length)) % rows.length]?.id ?? "missing",
  }));
  edges.push({ id: "missing", source: "missing", target: "m0:f0" });
  return { boxes, edges };
}

for (const [dx, dy] of [
  [0, 0],
  [11, 7],
  [470, 0],
  [-144, -80],
] as const) {
  it(`pins every route coordinate and omission at translation ${String(dx)}, ${String(dy)}`, () => {
    const { boxes, edges } = fixture();
    for (const box of boxes.filter((b) => b.top === "m0")) {
      box.x1 += dx;
      box.x2 += dx;
      box.y1 += dy;
      box.y2 += dy;
      box.anchorY += dy;
    }
    const routes = routeEdges(boxes, edges, tuningFrom(themes.dark));
    const coordinates = edges.map(({ id }) => [id, routes.get(id) ?? null]);
    // Hash all endpoint offsets, bend weights/distances and missing routes without rounding.
    expect(
      createHash("sha256").update(JSON.stringify(coordinates)).digest("hex"),
    ).toMatchSnapshot();
  });
}
