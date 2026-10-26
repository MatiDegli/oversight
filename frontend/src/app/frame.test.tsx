import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Frame } from "./Frame";
import type { FrameProps } from "./Frame";
import { through } from "./replay.fixture";
import { initialView, openChange } from "./view";
import type { View } from "./view";

// Monaco needs a browser (pnpm test:browser); the frame's markup does not.
vi.mock("../editor/monaco", () => ({ monaco: { editor: {} } }));

const diff = { changeId: "change-1", path: "shop/pricing.py" };

function render(seq: number, view: View = initialView, props: Partial<FrameProps> = {}) {
  const state = through(seq);
  return renderToStaticMarkup(
    <Frame
      state={state}
      view={view}
      onNodeTap={vi.fn()}
      canOpenChange={(changeId) => openChange(state, view, changeId) !== null}
      onOpenChange={vi.fn()}
      onClosePanel={vi.fn()}
      {...props}
    />,
  );
}

/** The opening tag of the first element with this aria-label. */
function tag(markup: string, label: string) {
  return new RegExp(`<[a-z]+ [^>]*aria-label="${label}"[^>]*>`).exec(markup)?.[0];
}
const queue = (markup: string) =>
  /<div role="status" aria-label="Review queue"[^>]*>(.*?)<\/div>/.exec(markup)?.[1];

describe("the frame over the level 1 fixture", () => {
  it("lays out watch mode: top bar, view rail, graph and the agents panel with one card", () => {
    const markup = render(14);
    for (const text of ["Oversight", "demo-repo", "main", ">8cbf753<", "Agent A"])
      expect(markup).toContain(text);
    expect(tag(markup, "Views")).toContain("w-(--left-rail-width)");
    expect(tag(markup, "Agents")).toContain("w-(--agents-panel-width)");
    expect(markup).toContain('aria-label="Repository graph"');
    expect(markup.match(/<article aria-label="Agent A"/g)).toHaveLength(1);
    expect(tag(markup, "Watch")).toContain('aria-current="page"');
    expect(markup).not.toContain("Live diff panel");
  });

  it("offers the card's Live diff only for a pending change with a file, and no decisions", () => {
    expect(render(4)).not.toContain("Live diff</button>");
    expect(render(14)).toContain("Live diff</button>");
    expect(render(22)).not.toContain("Live diff</button>");
    for (const seq of [14, 20]) {
      const markup = render(seq);
      for (const dead of ["Approve", "Reject", "Review change"]) expect(markup).not.toContain(dead);
    }
  });

  it("shows the ready pill in the top bar and on the card while nothing else changes", () => {
    const before = render(18);
    const ready = render(20);
    expect(queue(before)).toBe("");
    expect(queue(ready)).toContain("change ready for review");
    expect(ready).toContain('role="status" class="ml-auto rounded-full');
    expect(ready).toContain("Ready for review");
    // The layout and the graph are the same markup: only the counter and the card changed.
    for (const label of ["Views", "Agents", "Repository graph"])
      expect(tag(ready, label)).toBe(tag(before, label));
    expect(ready).not.toContain("Live diff panel");
    expect(queue(render(21))).toBe("");
  });

  it("opens the live diff on the left and folds the agents panel into the rail", () => {
    const markup = render(14, { selected: "shop.pricing:compute_price", diff });
    expect(tag(markup, "Live diff panel")).toContain("w-[min(var(--review-panel-width),46vw)]");
    expect(markup).toContain('aria-label="Live diff"');
    expect(markup).toContain("shop/pricing.py");
    expect(markup).toContain('aria-label="Close live diff"');
    expect(tag(markup, "Agents")).toContain("w-(--agents-rail-width)");
    expect(markup).toContain('aria-label="Agent A: Working"');
    expect(markup).not.toContain('<article aria-label="Agent A"');
    expect(tag(markup, "Watch")).not.toContain("aria-current");
    // The graph region is the same element in both modes, so it is never remounted.
    expect(tag(markup, "Repository graph")).toBe(tag(render(14), "Repository graph"));
    expect(render(20, { selected: null, diff })).toContain(
      'aria-label="Agent A: Ready for review"',
    );
  });
});
