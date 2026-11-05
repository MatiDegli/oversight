import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import { isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

import { applyEvent, createInitialState, selectLiveDiff, stateStore } from "../state";
import type { State, StateStore } from "../state";
import { LiveDiff } from "./LiveDiff";

vi.mock("./monaco", () => ({ monaco: { editor: {} } }));
vi.mock("zustand", () => ({
  useStore: (store: { getState: () => StateStore }, selector: (value: StateStore) => unknown) =>
    selector(store.getState()),
}));

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());

const editing =
  "This diff updates with each edit. Decisions open when the agent proposes the change.";
const proposed = "Agent A proposed this change. Review it to approve, request changes or reject.";

function html(value: State, onReview?: (changeId: string) => void) {
  stateStore.setState({ state: value });
  return renderToStaticMarkup(
    <LiveDiff data={selectLiveDiff(value, "change-1", "shop/pricing.py")} onReview={onReview} />,
  );
}

const text = (markup: string) => markup.replace(/<[^>]+>/g, "").replace(/\s+/g, " ");

type Props = { children?: ReactNode; onClick?: () => void; [key: string]: unknown };

/** The rendered tree's own buttons; components below it are not called, nor needed. */
function buttons(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(buttons);
  if (!isValidElement<Props>(node)) return [];
  const own = node.type === "button" ? [node] : [];
  return [...own, ...buttons(node.props.children)];
}

function reviewButton(value: State, onReview: (changeId: string) => void) {
  stateStore.setState({ state: value });
  const tree = LiveDiff({
    data: selectLiveDiff(value, "change-1", "shop/pricing.py"),
    onReview,
  });
  return buttons(tree).find((button) => text(renderToStaticMarkup(button)) === "Review change");
}

it("keeps the editing note and offers no review while the agent works", () => {
  const markup = text(html(through(12), () => {}));
  expect(markup).toContain(editing);
  expect(markup).not.toContain("proposed this change");
  expect(markup).not.toContain("Review change");
});

it("keeps the editing note until the agent's status says it is ready for review", () => {
  // At seq 19 the proposal exists but the agent is still working: the header says Working.
  const markup = text(html(through(19), () => {}));
  expect(markup).toContain(editing);
  expect(markup).not.toContain("Review change");
});

it("says who proposed the change and offers its review once it is ready", () => {
  const markup = text(html(through(20), () => {}));
  expect(markup).toContain(proposed);
  expect(markup).toContain("Review change");
  expect(markup).not.toContain("Decisions open when");
});

it("opens that change's review on a click, as a plain tab stop that never takes focus", () => {
  const onReview = vi.fn();
  const button = reviewButton(through(20), onReview);
  expect(button).toBeDefined();
  expect(button?.props.type).toBe("button");
  expect(button?.props.autoFocus).toBeUndefined();
  expect(button?.props.tabIndex).toBeUndefined();
  expect(String(button?.props.className)).toContain("border-control-edge");
  expect(onReview).not.toHaveBeenCalled();
  button?.props.onClick?.();
  expect(onReview).toHaveBeenCalledExactlyOnceWith("change-1");
});

it("shows the note without a button when no review action is given", () => {
  const markup = text(html(through(20)));
  expect(markup).toContain(proposed);
  expect(markup).not.toContain("Review change");
});
