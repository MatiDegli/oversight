import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { createInitialState } from "../../state";
import { surfaceTab, tabbedView, viewReducer } from "../view";
import { FocusPanel } from "./FocusPanel";

it("keeps a cold Settings open inside its existing heading until the body loads", () => {
  const html = renderToStaticMarkup(
    <FocusPanel
      state={createInitialState()}
      view={viewReducer(tabbedView, { type: "open", tab: surfaceTab(tabbedView, "settings") })}
      onNodeTap={vi.fn()}
      canOpenChange={() => false}
      onOpenChange={vi.fn()}
      onClosePanel={vi.fn()}
      startAgentAction={null}
      maximized={false}
      onMaximize={vi.fn()}
    />,
  );
  expect(html).toContain('aria-labelledby="merge-mode-heading"');
  expect(html).toContain('id="merge-mode-heading"');
  expect(html).toContain("Who lets a finished change into main.");
  expect(html).not.toContain("Every change waits for your decision.");
  expect(html).not.toContain("Setting the merge mode is not available in a replay.");
});
