import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { createInitialState } from "../../state";
import { through } from "../replay.fixture";
import { initialView } from "../view";
import { StatusLine } from "./Status";
import { selectBudgetLine } from "./statusSelectors";

it("reports the actual percentage at every brake level in neutral words", () => {
  const state = through(18);
  if (!state.run || !state.usage) throw new Error("Missing fixture");
  for (const [spent, brake] of [
    [1, "ok"],
    [50, "warn_50"],
    [80, "warn_80"],
    [91, "blocked"],
  ] as const) {
    expect(
      selectBudgetLine({
        ...state,
        usage: { ...state.usage, budget_usd: 100, spent_usd: spent, brake },
      }),
    ).toBe(`Budget ${String(spent)}% · brake at 90%`);
  }
  expect(
    selectBudgetLine({
      ...state,
      usage: null,
      run: { ...state.run, budget: { ...state.run.budget, spent_usd: 3, budget_usd: 100 } },
    }),
  ).toBe("Budget 3% · brake at 90%");
  expect(selectBudgetLine({ ...state, spendUnknown: true })).toBe("Budget unknown · brake at 90%");
  expect(selectBudgetLine(createInitialState())).toBeNull();
});

it("shows live or the supplied named replay once, the neutral budget and connection recovery", () => {
  const props = {
    state: through(18),
    view: initialView,
    onNodeTap: vi.fn(),
    canOpenChange: () => false,
    onOpenChange: vi.fn(),
    onClosePanel: vi.fn(),
    dock: false,
    onToggleDock: vi.fn(),
  };
  for (const connection of ["connected", "connecting", "lost"] as const) {
    const html = renderToStaticMarkup(
      <StatusLine {...props} connection={connection} onRetry={vi.fn()} />,
    );
    expect(html).toContain(">Live</span>");
    expect(html).toContain(
      { connected: "Connected", connecting: "Connecting", lost: "Connection lost" }[connection],
    );
    expect(html.includes("Retry connection")).toBe(connection === "lost");
    expect(html).toContain("brake at 90%");
    expect(html).not.toMatch(/text-agent|text-failed|text-passed|Merge mode/);
  }
  const label = "Recorded run · Oct 27, 2026";
  const html = renderToStaticMarkup(
    <StatusLine {...props} replayOf="recorded" replayLabel={label} />,
  );
  expect(html.split(label)).toHaveLength(2);
});
