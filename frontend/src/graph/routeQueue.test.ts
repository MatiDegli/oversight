import { expect, it } from "vitest";

import { RouteQueue } from "./routeQueue";

it("keeps the original cost/key order while the search adds and removes entries", () => {
  const queue = new RouteQueue();
  const reference: { key: number; cost: number }[] = [];
  for (let i = 0; i < 1000; i++) {
    const entry = { key: i, cost: (i * 37) % 31 };
    queue.push(entry);
    reference.push(entry);
    if (i % 3 === 0) {
      reference.sort((a, b) => b.cost - a.cost || b.key - a.key);
      expect(queue.pop()).toEqual(reference.pop());
    }
  }
  reference.sort((a, b) => b.cost - a.cost || b.key - a.key);
  while (reference.length) expect(queue.pop()).toEqual(reference.pop());
  expect(queue.length).toBe(0);
  expect(queue.pop()).toBeUndefined();
});
