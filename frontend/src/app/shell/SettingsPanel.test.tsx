import { Children, isValidElement, type ReactNode, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { createInitialState } from "../../state";
import { SettingsPanel, mergeModePending } from "./SettingsPanel";

const state = { ...createInitialState(), mergeMode: { mode: "ask_me", revision: 0 } as const };
function buttons(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  return Children.toArray(node).flatMap((child) => {
    if (!isValidElement<{ children?: ReactNode }>(child)) return [];
    return child.type === "button"
      ? [child as ReactElement<Record<string, unknown>>]
      : buttons(child.props.children);
  });
}
it.each([null, "ask_me", "merge_when_safe"] as const)(
  "marks only the recorded %s choice",
  (mode) => {
    const tree = SettingsPanel({
      state: { ...state, mergeMode: mode ? { mode, revision: 0 } : null },
    });
    expect(buttons(tree).map((b) => b.props["aria-pressed"])).toEqual([
      mode === "ask_me",
      mode === "merge_when_safe",
    ]);
    const html = renderToStaticMarkup(tree);
    for (const words of [
      "Merge mode",
      "Who lets a finished change into main.",
      "Every change waits for your decision.",
      "A change merges by itself only when every rule holds. Otherwise it waits for you.",
    ])
      expect(html).toContain(words);
    expect(html).not.toMatch(/Saved|DEMO-REPO|Applies to|THE RULES/);
  },
);
it("acts once on an unmarked pointer click, never on mount, marked or detail-zero clicks", () => {
  const onSetMergeMode = vi.fn();
  const tree = SettingsPanel({ state, onSetMergeMode });
  SettingsPanel({
    state: { ...state, mergeMode: { mode: "merge_when_safe", revision: 1 } },
    onSetMergeMode,
  });
  expect(onSetMergeMode).not.toHaveBeenCalled();
  const [ask, rules] = buttons(tree);
  const click = (button: typeof ask, detail: number) => {
    (button?.props.onClick as (e: { detail: number }) => void)({ detail });
  };
  click(ask, 1);
  click(rules, 0);
  expect(onSetMergeMode).not.toHaveBeenCalled();
  click(rules, 1);
  expect(onSetMergeMode).toHaveBeenCalledExactlyOnceWith("merge_when_safe");
});
it("keeps pending until both HTTP and the state revision arrive", () => {
  const attempt = { run: state.run, revision: 0, phase: "pending" } as const;
  expect(mergeModePending(state, attempt)).toBe(true);
  expect(mergeModePending(state, { ...attempt, responseRevision: 1 })).toBe(true);
  const next = { ...state, mergeMode: { mode: "ask_me", revision: 1 } as const };
  expect(mergeModePending(next, attempt)).toBe(true);
  expect(mergeModePending(next, { ...attempt, responseRevision: 1 })).toBe(false);
  expect(buttons(SettingsPanel({ state, attempt })).every((b) => b.props.disabled)).toBe(true);
});
it("shows a refusal until revision changes and labels read-only replay", () => {
  const message = "Merge mode revision is stale; read the current mode again";
  const attempt = { run: state.run, revision: 0, phase: "failed", message } as const;
  const html = renderToStaticMarkup(
    <SettingsPanel state={state} attempt={attempt} onSetMergeMode={() => {}} />,
  );
  expect(html).toContain(`role="alert"`);
  expect(html).toContain(message);
  expect(
    renderToStaticMarkup(
      <SettingsPanel
        state={{ ...state, mergeMode: { mode: "ask_me", revision: 1 } }}
        attempt={attempt}
      />,
    ),
  ).not.toContain(message);
  expect(renderToStaticMarkup(<SettingsPanel state={state} />)).toContain(
    "Setting the merge mode is not available in a replay.",
  );
});
