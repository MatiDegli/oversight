import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { headerState } from "./ReviewHeader.fixture";
import { RevertControl } from "./RevertControl";
import { revertView, type RevertAttempt } from "./revert";

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
