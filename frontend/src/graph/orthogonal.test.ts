import { expect, it } from "vitest";

import { crosses, obstacleChecks } from "./orthogonal";

it("indexes exactly the same intersections as a scan, including borders and endpoint exemptions", () => {
  const boxes = Array.from({ length: 50 }, (_, i) => ({
    id: i,
    x1: (i % 7) * 90,
    x2: (i % 7) * 90 + 60,
    y1: Math.floor(i / 7) * 80,
    y2: Math.floor(i / 7) * 80 + 60,
  }));
  const blocked = obstacleChecks(boxes);
  for (let i = 0; i < 2000; i++) {
    const a = { x: (i % 70) * 10, y: (i % 80) * 10 };
    const b = i % 2 ? { x: a.x, y: 600 - a.y } : { x: 700 - a.x, y: a.y };
    const except = (box: (typeof boxes)[number]) => box.id % 3 === 0;
    expect(blocked(a, b)).toBe(boxes.some((box) => crosses(a, b, box)));
    expect(blocked(a, b, except)).toBe(boxes.some((box) => !except(box) && crosses(a, b, box)));
  }
});
