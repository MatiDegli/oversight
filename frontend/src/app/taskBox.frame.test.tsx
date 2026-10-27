import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Frame } from "./Frame";
import type { FrameProps } from "./Frame";
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
  it("is the first thing under the heading while there are no agents", () => {
    const markup = render(2, live);
    expect(markup.indexOf(">Agents</h2>")).toBeLessThan(markup.indexOf(">New agent</h3>"));
    expect(markup.indexOf(">New agent</h3>")).toBeLessThan(markup.indexOf("No agents yet"));
    expect(markup).not.toContain("<article");
    expect(markup).toContain("<textarea");
  });

  it("sits below the agent cards, and says why when no slot is free", () => {
    const markup = render(4, live, ["A"]);
    expect(markup.indexOf("<article")).toBeLessThan(markup.indexOf(">New agent</h3>"));
    expect(markup).not.toContain("No agents yet");
    expect(markup).toContain("Agent A is working.");
    // The recorded run announces slots A and B: B stays free while A works.
    expect(render(4, live)).not.toContain("Agent A is working.");
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
