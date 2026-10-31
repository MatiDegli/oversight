import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { createInitialState } from "../../state";
import { through } from "../replay.fixture";
import { StatusLine } from "./Status";
import { selectMergeModeLine } from "./statusSelectors";
import { initialView } from "../view";

it.each([
  [null, null],
  ["ask_me", "Merge mode: Ask me"],
  ["merge_when_safe", "Merge mode: Merge alone when safe"],
] as const)("shows the %s mode after the unchanged live or replay segment", (mode, line) => {
  const state = {
    ...createInitialState(),
    run: through(1).run,
    mergeMode: mode ? { mode, revision: 0 } : null,
  };
  expect(selectMergeModeLine(state)).toBe(line);
  for (const replayOf of [undefined, "real recorded run"]) {
    const html = renderToStaticMarkup(
      <StatusLine
        state={state}
        view={initialView}
        dock={false}
        onToggleDock={() => {}}
        onNodeTap={() => {}}
        canOpenChange={() => false}
        onOpenChange={() => {}}
        onClosePanel={() => {}}
        replayOf={replayOf}
      />,
    );
    expect(html).toContain(
      `<span>${replayOf ? "Replay · real recorded run · Real recorded run" : "Live"}</span>`,
    );
    const budget = '<span role="status" class="text-muted">Budget 0% · brake at 90%</span>';
    expect(html).toContain(budget);
    if (line) {
      expect(html.indexOf(line)).toBeGreaterThan(html.indexOf("</span>"));
      expect(html.indexOf(line)).toBeLessThan(html.indexOf(budget));
      expect(html).toContain("lucide-git-merge");
    } else expect(html).not.toContain("Merge mode:");
  }
});
