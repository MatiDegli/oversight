import { renderToReadableStream, renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { Frame } from "./Frame";
import type { FrameProps } from "./Frame";
import { through } from "./replay.fixture";
import { startAttempt } from "../review";
import { initialView, openChange, openReview } from "./view";
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

// Settle the real lazy review before checking its fully loaded static markup.
beforeAll(async () => {
  const graph = await renderToReadableStream(
    <Frame
      state={through(14)}
      view={initialView}
      onNodeTap={vi.fn()}
      canOpenChange={() => false}
      onOpenChange={vi.fn()}
      onClosePanel={vi.fn()}
    />,
  );
  await graph.allReady;
  await new Response(graph).text();
  const state = through(20);
  const abort = new AbortController();
  const stream = await renderToReadableStream(
    <Frame
      state={state}
      view={openReview(state, initialView, "change-1") ?? initialView}
      onNodeTap={vi.fn()}
      canOpenChange={() => true}
      onOpenChange={vi.fn()}
      onClosePanel={vi.fn()}
    />,
    { signal: abort.signal, onError: () => {} },
  );
  const reader = (stream as ReadableStream<Uint8Array>).getReader();
  let html = "";
  while (!html.includes('class="review-body')) {
    const { value, done } = await reader.read();
    if (done) throw new Error("Review did not load");
    html += new TextDecoder().decode(value);
  }
  abort.abort();
  await reader.cancel();
});

/** The opening tag of the first element with this aria-label. */
function tag(markup: string, label: string) {
  return new RegExp(`<[a-z]+ [^>]*aria-label="${label}"[^>]*>`).exec(markup)?.[0];
}
const queue = (markup: string) =>
  /<div role="status" aria-label="Review queue"[^>]*>(.*?)<\/div>/.exec(markup)?.[1];

describe("the frame over the level 1 fixture", () => {
  it("keeps B's letter, model and token colors on its card and folded rail", () => {
    const state = through(20);
    const a = state.agents["agent-1"];
    if (!a) throw new Error("Missing agent fixture");
    state.agents = {
      ...state.agents,
      "agent-2": {
        ...a,
        agent_id: "agent-2",
        change_id: "change-2",
        slot: "B",
        model: "muse-spark-open",
      },
    };
    const markup = (view: View) => render(20, view, { state });
    const card = markup(initialView);
    const bCard = card.slice(card.indexOf('<article aria-label="Agent B"'));
    // The card names the model as the run does ("Muse Spark Open"), not by its id.
    expect(bCard).toContain(">Muse Spark Open<");
    expect(bCard).not.toContain("muse-spark-open");
    for (const token of ["text-agent-b-text", "bg-agent-b-tint", "border-agent-b", "bg-agent-b"])
      expect(bCard).toContain(token);
    const rail = markup({ selected: null, diff, review: null, file: null });
    const bRail = rail.slice(rail.indexOf('aria-label="Agent B: Ready for review"'));
    expect(bRail).toContain("bg-agent-b-tint text-agent-b-text");
    expect(bRail).toMatch(/>B<span aria-hidden="true"/);
  });
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

  it("overlays the graph with the heading, the legend and the zoom controls", () => {
    const markup = render(14);
    for (const text of ["6 modules", "36 functions", "1 agent working", "Possible call", "Zoom in"])
      expect(markup).toContain(text);
    // In review the cone card takes the heading's place; the legend and the zoom stay.
    const state = through(20);
    const review = openReview(state, initialView, "change-1");
    if (!review) throw new Error("Missing recorded review");
    const reviewing = render(20, review);
    expect(reviewing).not.toContain("agents working");
    for (const text of ["Possible call", "Zoom in"]) expect(reviewing).toContain(text);
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
    expect(queue(before)).toContain("Nothing needs you");
    expect(queue(ready)).toContain("Decisions ·");
    expect(ready).toContain('role="status" class="ml-auto rounded-full');
    expect(ready).toContain("Ready for review");
    // The layout and the graph are the same markup: only the counter and the card changed.
    for (const label of ["Views", "Agents", "Repository graph"])
      expect(tag(ready, label)).toBe(tag(before, label));
    expect(ready).not.toContain("Live diff panel");
    expect(queue(render(21))).toContain("Nothing needs you");
  });

  it("opens the live diff on the left and folds the agents panel into the rail", () => {
    const markup = render(14, {
      selected: "shop.pricing:compute_price",
      diff,
      review: null,
      file: null,
    });
    expect(tag(markup, "Live diff panel")).toContain("w-[min(var(--review-panel-width),46vw)]");
    // The heading is a watch overlay: beside the panel it would sit on the graph's own nodes.
    expect(markup).not.toContain("agent working");
    expect(markup).not.toContain("6 modules");
    expect(markup).toContain('aria-label="Live diff panel"');
    expect(markup).toContain("Loading editor…");
    expect(markup).toContain('aria-label="Close live diff"');
    expect(tag(markup, "Agents")).toContain("w-(--agents-rail-width)");
    expect(markup).toContain('aria-label="Agent A: Working"');
    expect(markup).not.toContain('<article aria-label="Agent A"');
    expect(tag(markup, "Watch")).not.toContain("aria-current");
    // The graph region is the same element in both modes, so it is never remounted.
    expect(tag(markup, "Repository graph")).toBe(tag(render(14), "Repository graph"));
    expect(render(20, { selected: null, diff, review: null, file: null })).toContain(
      'aria-label="Agent A: Ready for review"',
    );
  });
});

describe("review mode over the level 1 fixture", () => {
  const review: View = {
    selected: "shop.pricing:compute_price",
    diff: null,
    review: "change-1",
    file: null,
  };
  const decide = vi.fn();
  const reviewing = (seq: number, props: Partial<FrameProps> = {}) =>
    render(seq, review, { onDecide: decide, ...props });
  /** The opening tag of the button whose text includes `text`. */
  const button = (markup: string, text: string) =>
    new RegExp(`<button [^>]*>(?:(?!</button>).)*${text}`).exec(markup)?.[0];

  it("offers Review change on the card and the queue counter once the change is proposed", () => {
    const state = through(20);
    const canReview = (changeId: string) => openReview(state, initialView, changeId) !== null;
    const ready = render(20, initialView, { canReview, onReview: vi.fn(), onOpenQueue: vi.fn() });
    expect(ready).toContain("Review change</button>");
    expect(ready).not.toContain("Live diff</button>");
    expect(button(ready, "Decisions ·")).not.toContain('disabled=""');
    const working = render(14, initialView, { canReview: () => false, onReview: vi.fn() });
    expect(working).toContain("Live diff</button>");
    expect(working).not.toContain("Review change");
    // Nothing opens review by itself: a proposed change leaves watch mode as it was.
    expect(ready).not.toContain("Review panel");
  });

  it("shows the header, facts, note, diff and decisions on the left, with the agents rail", () => {
    const markup = reviewing(20);
    expect(tag(markup, "Review panel")).toContain("w-[min(var(--review-panel-width),46vw)]");
    expect(tag(markup, "Review panel")).toContain("bg-page");
    expect(markup.replace(/<[^>]*>/g, "")).toContain("28 passed");
    for (const text of [
      "Cap the combined discount at 30% in compute_price",
      'aria-label="Agent A"',
      "Ready for review",
      'aria-label="Close review"',
      "+2",
      "compute_price now caps",
      'aria-label="Diff of shop/pricing.py"',
      "Comment for the agent",
      "If main moved since the agent started, the tests run again",
    ])
      expect(markup).toContain(text);
    expect(button(markup, "Approve and merge")).not.toContain('disabled=""');
    expect(button(markup, "Reject")).not.toContain('disabled=""');
    expect(button(markup, "Request changes")).toContain('disabled=""');
    expect(markup).not.toContain("Live diff panel");
    expect(tag(markup, "Agents")).toContain("w-(--agents-rail-width)");
    expect(tag(markup, "Repository graph")).toBe(tag(render(20), "Repository graph"));
  });

  it("disables both buttons while a decision is pending and shows a refusal's message", () => {
    const pending = reviewing(20, {
      attempts: { "change-1": startAttempt(through(20), "change-1", "approve") },
    });
    expect(button(pending, "Approve and merge")).toContain('disabled=""');
    expect(button(pending, "Reject")).toContain('disabled=""');
    expect(pending).toContain("Approving: waiting for the merge result.");
    const refused = reviewing(20, {
      attempts: {
        "change-1": { phase: "failed", action: "approve", revision: 1, message: "Main moved." },
      },
    });
    expect(button(refused, "Approve and merge")).not.toContain('disabled=""');
    expect(refused).toMatch(/role="alert"[^>]*>.*Main moved\./);
  });

  it("keeps review open after the decision and shows its result, with no buttons", () => {
    const merged = reviewing(23, {
      attempts: { "change-1": startAttempt(through(20), "change-1", "approve") },
    });
    expect(merged).toContain("Approved and merged into main");
    expect(merged).toContain(">040a486<");
    expect(merged).not.toContain("Approve and merge");
    expect(merged).not.toContain("Reject</button>");
  });

  it("in a replay sends nothing: disabled buttons, then the decision the log holds", () => {
    const replayOf = "hand-written demo fixture";
    const before = render(20, review, { replayOf });
    expect(before).toContain(
      "Replay of a hand-written demo fixture: nothing here sends a decision.",
    );
    expect(before).not.toMatch(/recorded run/i);
    expect(before).not.toContain("<textarea");
    expect(button(before, "Approve and merge")).toContain('disabled=""');
    expect(button(before, "Reject")).toContain('disabled=""');
    const after = render(21, review, { replayOf });
    expect(after).toContain(
      "Replay of a hand-written demo fixture: this is the decision the log holds.",
    );
    expect(after).not.toMatch(/recorded run/i);
    expect(after).toContain("Approved and merged into main");
    // A real recording says so by its own source, never by a default.
    expect(render(21, review, { replayOf: "real recorded run" })).toContain(
      "Replay of a real recorded run: this is the decision the log holds.",
    );
  });
});

it("puts the file view in the same panel slot, folds the rail, and gives review and diff priority", () => {
  const files = {
    drafts: new Map(),
    inFlightSaves: new Map(),
    saved: new Map(),
    readFile: vi.fn(),
    saveFile: vi.fn(),
  };
  const view: View = {
    ...initialView,
    file: { path: "shop/cart.py", agentId: null, startLine: 5, endLine: 9 },
  };
  const markup = render(20, view, { files });
  expect(tag(markup, "File view")).toContain("w-[min(var(--review-panel-width),46vw)]");
  expect(tag(markup, "Agents")).toContain("w-(--agents-rail-width)");
  expect(tag(markup, "Repository graph")).toBe(tag(render(20), "Repository graph"));
  expect(markup).not.toContain("Live diff panel");
  expect(markup).not.toContain("Review panel");
  expect(markup).not.toContain("6 modules");
  expect(markup).toContain("Possible call");
  const reviewing = render(20, { ...view, review: "change-1" }, { files });
  expect(reviewing).toContain('aria-label="Review panel"');
  expect(reviewing).not.toContain('aria-label="File view"');
  const live = render(14, { ...view, diff }, { files });
  expect(live).toContain('aria-label="Live diff panel"');
  expect(live).not.toContain('aria-label="File view"');
});
