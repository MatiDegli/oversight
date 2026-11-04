import { Children, isValidElement, type ReactNode, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
import { createInitialState } from "../../state";
import { previewStartup, type PreviewStartupResult } from "../preview/previewStartup";
import { SettingsPanel } from "./SettingsPanel";
import { mergeModePending } from "./mergeModeAttempt";

vi.mock("../preview/previewStartup", () => ({ previewStartup: vi.fn() }));
beforeEach(() => vi.mocked(previewStartup).mockReset().mockReturnValue({ kind: "missing" }));

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
      "Preview startup",
      "Not enabled on this server.",
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
  for (const card of buttons(tree)) {
    expect(card.props.onKeyDown).toBeUndefined();
    expect(card.props.onKeyUp).toBeUndefined();
  }
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
  const onSetMergeMode = vi.fn();
  const cards = buttons(SettingsPanel({ state, attempt, onSetMergeMode }));
  expect(cards.every((b) => b.props["aria-disabled"])).toBe(true);
  expect(cards.every((b) => b.props.disabled === undefined)).toBe(true);
  for (const card of cards) {
    (card.props.onClick as (e: { detail: number }) => void)({ detail: 1 });
  }
  expect(onSetMergeMode).not.toHaveBeenCalled();
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

it.each<PreviewStartupResult>([
  { kind: "missing" },
  { kind: "invalid" },
  {
    kind: "valid",
    report: {
      version: 1,
      argv: [["pnpm", "build"]],
      cwd: "/workspace/app",
      reason: "Settings fixture",
      dependencies: [],
      limits: "256 MiB; 300 s",
      platform_residual: "",
      build_log: "/runtime/build.log",
      enable_hint: "Set OVERSIGHT_PREVIEW_ENABLED=1",
      agents_label: "Agent A",
    },
  },
])("mounts the $kind disclosure after merge mode through the shared reader", (result) => {
  vi.mocked(previewStartup).mockReturnValue(result);
  const html = renderToStaticMarkup(<SettingsPanel state={state} />);
  expect(previewStartup).toHaveBeenCalledExactlyOnceWith();
  expect(html).toContain('aria-labelledby="settings-preview-startup-heading"');
  expect(html.match(/id="settings-preview-startup-heading"/gu)).toHaveLength(1);
  expect(html.match(/<h2\b/gu)).toHaveLength(5);
  expect(html).not.toContain(">Settings</h2>");
  expect(html.indexOf("Preview startup")).toBeGreaterThan(
    html.indexOf("Setting the merge mode is not available in a replay."),
  );
  const message =
    result.kind === "valid"
      ? "Settings fixture"
      : result.kind === "missing"
        ? "Not enabled on this server."
        : "The preview startup report is invalid, so reviewed-app previews stay off.";
  expect(html).toContain(message);
  const onSetMergeMode = vi.fn();
  const cards = buttons(SettingsPanel({ state, onSetMergeMode }));
  expect(cards).toHaveLength(2);
  expect(cards.map((card) => card.props["aria-pressed"])).toEqual([true, false]);
  expect(onSetMergeMode).not.toHaveBeenCalled();
});
