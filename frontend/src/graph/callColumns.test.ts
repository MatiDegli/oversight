import { expect, it } from "vitest";

import { callColumns } from "../../tools/callColumns.ts";

it("uses call depth, leaving shallow sinks beside their callers and the hub at the right", () => {
  const calls = [
    ["orders", "cart"],
    ["orders", "inventory"],
    ["cart", "models"],
    ["models", "pricing"],
  ] as const;
  const ids = ["orders", "cart", "inventory", "models", "pricing"];
  expect(Object.fromEntries(callColumns(ids, calls))).toEqual({
    orders: 0,
    cart: 1,
    inventory: 1,
    models: 2,
    pricing: 3,
  });
});

it("condenses cycles before computing depth, regardless of traversal order", () => {
  const calls = [
    ["a", "b"],
    ["b", "a"],
    ["b", "c"],
    ["c", "d"],
  ] as const;
  const ids = ["a", "b", "c", "d"];
  const expected = { a: 0, b: 0, c: 1, d: 2 };
  expect(Object.fromEntries(callColumns(ids, calls))).toEqual(expected);
  expect(Object.fromEntries(callColumns([...ids].reverse(), [...calls].reverse()))).toEqual(
    expected,
  );
});
