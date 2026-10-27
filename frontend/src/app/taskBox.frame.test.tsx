import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Frame } from "./Frame";
import type { FrameProps } from "./Frame";
import { createInitialState } from "../state";
import { through } from "./replay.fixture";
import { initialView } from "./view";

// Monaco needs a browser (pnpm test:browser); the frame's markup does not.
vi.mock("../editor/monaco", () => ({ monaco: { editor: {} } }));

/** The recorded run up to `seq`; `slots` limits it to the slots a level 1 server announces. */
function render(seq: number, props: Partial<FrameProps> = {}, slots?: ("A" | "B")[]) {
  const state = through(seq);
  const run = state.run;
  return renderToStaticMarkup(
    <Frame
      state={
        run && slots
          ? {
              ...state,
              run: { ...run, slots: run.slots.filter((info) => slots.includes(info.slot)) },
            }
          : state
      }
      view={initialView}
      onNodeTap={vi.fn()}
      canOpenChange={() => false}
      onOpenChange={vi.fn()}
      onClosePanel={vi.fn()}
      {...props}
    />,
  );
}
const live = { onStartAgent: () => Promise.resolve(undefined) };

describe("the agents panel's new-agent section", () => {
  it("says there are no agents above the box while there are none", () => {
    const markup = render(2, live);
    expect(markup.indexOf(">Agents</h2>")).toBeLessThan(markup.indexOf("No agents yet"));
    expect(markup.indexOf("No agents yet")).toBeLessThan(markup.indexOf(">New agent</h3>"));
    expect(markup).not.toContain("<article");
    expect(markup).toContain("<textarea");
  });

  it("is pinned to the panel's bottom under a divider, outside the scrolling cards", () => {
    for (const seq of [2, 4, 20]) {
      const markup = render(seq, live);
      const panel = markup.slice(markup.indexOf('<aside aria-label="Agents"'));
      const pinned = /<div class="shrink-0 border-t border-divider[^"]*">/.exec(panel);
      expect(pinned, `seq ${String(seq)}`).not.toBeNull();
      const scrolling = panel.indexOf("overflow-y-auto");
      // The cards scroll in a region of their own, which ends before the pinned one begins.
      expect(scrolling).toBeGreaterThan(-1);
      expect(scrolling).toBeLessThan(pinned?.index ?? 0);
      expect(panel.indexOf("<article")).toBeLessThan(pinned?.index ?? 0);
      expect(panel.indexOf(">New agent</h3>")).toBeGreaterThan(pinned?.index ?? 0);
      expect(panel.slice(0, pinned?.index)).not.toContain("<textarea");
    }
  });

  it("counts the slots in use beside the heading, as the server offers them", () => {
    expect(render(2, live)).toContain("0 of 2 slots");
    // Agent A holds its slot while it works and while it waits for review.
    expect(render(4, live)).toContain("1 of 2 slots");
    expect(render(20, live)).toContain("1 of 2 slots");
    // A level 1 server offers one slot.
    expect(render(4, live, ["A"])).toContain("1 of 1 slot<");
    // Nothing to count before the run has started.
    const before = renderToStaticMarkup(
      <Frame
        state={createInitialState()}
        view={initialView}
        onNodeTap={vi.fn()}
        canOpenChange={() => false}
        onOpenChange={vi.fn()}
        onClosePanel={vi.fn()}
      />,
    );
    expect(before).not.toContain("slot");
  });

  it("sits below the agent cards, and says why when no slot is free", () => {
    const markup = render(4, live, ["A"]);
    expect(markup.indexOf("<article")).toBeLessThan(markup.indexOf(">New agent</h3>"));
    expect(markup).not.toContain("No agents yet");
    expect(markup).toContain("Agent A is working.");
    // The recorded run announces slots A and B: B stays free while A works.
    const two = render(4, live);
    expect(two).toContain("Agent A is working.");
    // The holder is now named on its slot button; there is no global start block.
    expect(two).toMatch(/<div role="status" id="[^"]+-line"[^>]*><\/div>/);
    expect(two).toContain('aria-pressed="true" aria-disabled="false"');
  });

  it("offers no live control in a replay, which passes no command, and words it from the source", () => {
    const replayOf = "hand-written demo fixture";
    for (const seq of [2, 4, 20]) {
      const markup = render(seq, { replayOf });
      expect(markup).toContain(
        "Replay of a hand-written demo fixture: agents cannot be started here.",
      );
      expect(markup).not.toMatch(/recorded run/i);
      expect(markup).not.toContain("<textarea");
      expect(markup).not.toContain("Start agent");
    }
    // A real recording says so by its own source, never by a default.
    expect(render(4, { replayOf: "real recorded run" })).toContain(
      "Replay of a real recorded run: agents cannot be started here.",
    );
  });
});
