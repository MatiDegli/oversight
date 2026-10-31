import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { headerState } from "./ReviewHeader.fixture";
import { RevertControl } from "./RevertControl";
import { revertView, type RevertAttempt } from "./revert";

function buttons(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  return Children.toArray(node).flatMap((child) => {
    if (!isValidElement<{ children?: ReactNode }>(child)) return [];
    return child.type === "button"
      ? [child as ReactElement<Record<string, unknown>>]
      : buttons(child.props.children);
  });
}

it("ignores detail-zero clicks and sends exactly once on a pointer click without key handlers", () => {
  const onRevert = vi.fn();
  const [button] = buttons(
    RevertControl({ state: headerState("merged"), changeId: "change-1", onRevert }),
  );
  if (!button) throw new Error("Missing Revert button");
  expect(onRevert).not.toHaveBeenCalled();
  expect(button.props.onKeyDown).toBeUndefined();
  expect(button.props.onKeyUp).toBeUndefined();
  const click = button.props.onClick as (event: { detail: number }) => void;
  click({ detail: 0 });
  expect(onRevert).not.toHaveBeenCalled();
  click({ detail: 1 });
  expect(onRevert).toHaveBeenCalledExactlyOnceWith("change-1");
});

it("sends nothing on a pointer click while a Revert is pending", () => {
  const state = headerState("merged");
  const view = revertView(state, "change-1");
  if (view.kind === "none") throw new Error("Missing merge");
  const onRevert = vi.fn();
  const [button] = buttons(
    RevertControl({
      state,
      changeId: "change-1",
      onRevert,
      attempt: { run: state.run, target: view.body, afterSeq: state.lastSeq, phase: "pending" },
    }),
  );
  if (!button) throw new Error("Missing Revert button");
  const click = button.props.onClick as (event: { detail: number }) => void;
  click({ detail: 1 });
  expect(onRevert).not.toHaveBeenCalled();
});

it("renders a focusable secondary Revert or the replay sentence, without reading mode", () => {
  const state = headerState("merged");
  Object.defineProperty(state, "mergeMode", {
    get() {
      throw new Error("Mode read");
    },
  });
  const render = (attempt?: RevertAttempt, live = true) =>
    renderToStaticMarkup(
      <RevertControl
        state={state}
        changeId="change-1"
        attempt={attempt}
        onRevert={live ? () => undefined : undefined}
      />,
    );
  expect(render()).toContain("lucide-undo-2");
  expect(render()).not.toContain("accesskey");
  expect(render(undefined, false)).toContain("Revert is not available in a replay.");
  const view = revertView(state, "change-1");
  if (view.kind === "none") throw new Error("Missing merge");
  const attempt: RevertAttempt = {
    run: state.run,
    target: view.body,
    afterSeq: state.lastSeq,
    phase: "pending",
  };
  expect(render(attempt)).toContain("Reverting: waiting for the result.");
  expect(render(attempt)).toContain('aria-disabled="true"');
  expect(render({ ...attempt, phase: "refused", message: "Main moved" })).toContain('role="alert"');
  const resolution = state.changes["change-1"]?.resolution;
  if (!resolution) throw new Error("Missing merge");
  resolution.decided_by = "person";
  expect(render()).toBe("");
});
