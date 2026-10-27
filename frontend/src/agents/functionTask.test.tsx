import type { GraphNode } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { twoSlotState } from "../taskBox.fixture";
import { StartAgentHere } from "./StartAgentHere";
import { functionTask } from "./functionTask";
import { createTaskBoxStore } from "./taskBox";

it.each([
  ["function", "shop/pricing.py", "compute_price", "In shop/pricing.py, compute_price: "],
  ["method", "shop/cart.py", "Cart.add", "In shop/cart.py, Cart.add: "],
  ["function", "src/shop/prices/rules.py", "cap", "In src/shop/prices/rules.py, cap: "],
])("prefills a %s in %s using its qualified name", (_kind, path, qualname, expected) => {
  expect(functionTask({ path, qualname })).toBe(expected);
});

it.each(["module", "class"] as const)("offers no action for a %s", (kind) => {
  const node: GraphNode = {
    id: "shop.cart:Cart",
    kind,
    name: "Cart",
    qualname: "Cart",
    path: "shop/cart.py",
    start_line: 1,
    end_line: 40,
    parent_id: null,
    is_test: false,
  };
  expect(
    renderToStaticMarkup(
      <StartAgentHere node={node} state={twoSlotState()} onOpen={() => undefined} />,
    ),
  ).toBe("");
});

it("fills only an empty idle store, preserving text, whitespace and a pending request", async () => {
  const store = createTaskBoxStore();
  expect(store.getState().prefill("In shop/cart.py, Cart.add: ")).toBe(true);
  expect(store.getState().prefill("Replace")).toBe(false);
  expect(store.getState().draft).toBe("In shop/cart.py, Cart.add: ");
  store.getState().setDraft(" ");
  expect(store.getState().prefill("Replace")).toBe(false);
  expect(store.getState().draft).toBe(" ");
  store.getState().setDraft("Keep the task");
  let finish: () => void = () => undefined;
  const pending = store.getState().start(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  store.getState().setDraft("");
  expect(store.getState().prefill("Replace")).toBe(false);
  finish();
  await pending;
});
