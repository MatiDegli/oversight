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
    examples: null,
  };
  expect(
    renderToStaticMarkup(
      <StartAgentHere node={node} state={twoSlotState()} onOpen={() => undefined} />,
    ),
  ).toBe("");
});

it("fills only an empty idle store, preserving text, whitespace and a pending request", async () => {
  const store = createTaskBoxStore();
  const nodeId = "shop.cart:Cart.add";
  expect(store.getState().prefill("In shop/cart.py, Cart.add: ", nodeId)).toBe(true);
  expect(store.getState().prefill("Replace", nodeId)).toBe(false);
  expect(store.getState().draft).toBe("In shop/cart.py, Cart.add: ");
  store.getState().setDraft(" ");
  expect(store.getState().prefill("Replace", nodeId)).toBe(false);
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
  expect(store.getState().prefill("Replace", nodeId)).toBe(false);
  finish();
  await pending;
});

it("keeps the prefill's node and text until the person edits or starts the task", async () => {
  const store = createTaskBoxStore();
  const text = "In shop/cart.py, Cart.add: ";
  const nodeId = "shop.cart:Cart.add";
  store.getState().prefill(text, nodeId);
  expect(store.getState().prefilled).toEqual({ nodeId, text });
  // A refused prefill from another node cannot claim the existing text.
  expect(store.getState().prefill("In shop/cart.py, Cart.remove: ", "shop.cart:Cart.remove")).toBe(
    false,
  );
  expect(store.getState().prefilled).toEqual({ nodeId, text });
  store.getState().setDraft(`${text}Keep my edit`);
  expect(store.getState().prefilled).toBeNull();
  store.getState().setDraft("");
  store.getState().setDraft(text);
  expect(store.getState().prefilled).toBeNull();
  store.getState().setDraft("");
  store.getState().prefill(text, nodeId);
  await store.getState().start(() => Promise.resolve());
  expect(store.getState()).toMatchObject({ draft: "", prefilled: null });
});
