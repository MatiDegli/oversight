import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";

import { Frame } from "./Frame";
import { through } from "./replay.fixture";
import { initialView, openReview } from "./view";
import type { View } from "./view";
import type { GraphViewProps } from "../graph";

const graph = vi.hoisted(() => ({ render: vi.fn() }));
vi.mock("../graph", async (original) => ({
  ...(await original<typeof import("../graph")>()),
  GraphView: (props: GraphViewProps) => {
    graph.render(props);
    return <div>Graph</div>;
  },
}));
vi.mock("../editor", () => ({
  EditorDiff: () => <div>Diff</div>,
  LiveDiff: () => <div>Live diff</div>,
}));

afterEach(() => vi.clearAllMocks());

function render(view: View, seq = 20) {
  return renderToStaticMarkup(
    <Frame
      state={through(seq)}
      view={view}
      onNodeTap={() => undefined}
      canOpenChange={() => false}
      onOpenChange={() => undefined}
      onClosePanel={() => undefined}
      notice={<p>Real recorded run</p>}
    />,
  );
}

it("passes the recorded cone to the same graph, with a card and a panel summary only in review", () => {
  const state = through(20);
  const view = openReview(state, initialView, "change-1");
  const proposal = state.changes["change-1"]?.proposal;
  if (!view || !proposal) throw new Error("Missing recorded review");
  const html = render(view);
  expect(html).toContain('aria-label="Possibly affected"');
  expect(html).toContain("9 functions possibly affected, up to distance 2");
  // The notice stays on the graph, under the cone card that takes the heading's place.
  expect(html.indexOf("Real recorded run")).toBeGreaterThan(
    html.indexOf('aria-label="Possibly affected"'),
  );
  expect(html).not.toContain("agents working");
  expect(graph.render).toHaveBeenCalledWith(
    expect.objectContaining({
      cone: proposal.impact,
      litIds: [
        "shop.pricing:compute_price",
        ...proposal.impact.entries.map((entry) => entry.node_id),
      ],
      frameIds: ["shop.pricing:compute_price"],
      frameKey: "review:change-1",
    }),
  );
});

it("leaves watch without a cone", () => {
  const html = render(initialView);
  expect(html).not.toContain('aria-label="Possibly affected"');
  expect(graph.render).toHaveBeenLastCalledWith(
    expect.objectContaining({ cone: null, litIds: null }),
  );
});

it("keeps live cone lights and dimming separate from the review card and camera scope", () => {
  const html = render(
    { ...initialView, diff: { changeId: "change-1", path: "shop/pricing.py" } },
    14,
  );
  const props = graph.render.mock.calls.at(-1)?.[0] as GraphViewProps;
  expect(html).not.toContain('aria-label="Possibly affected"');
  expect(props.cone).toBeNull();
  expect(props.liveCone?.entries.length).toBeGreaterThan(0);
  expect(
    props.field?.lights
      .filter((light) => light.source === "work-light")
      .map((light) => light.id)
      .sort(),
  ).toEqual(props.liveCone?.entries.map((entry) => entry.node_id).sort());
  expect(props.litIds).toEqual(
    expect.arrayContaining(props.liveCone?.entries.map((entry) => entry.node_id) ?? []),
  );
});

it("clears the graph cone after a decision while keeping the recorded panel summary", () => {
  const html = render({ ...initialView, review: "change-1" }, 21);
  expect(html).not.toContain('aria-label="Possibly affected"');
  expect(html).toContain("9 functions possibly affected, up to distance 2");
  expect(graph.render).toHaveBeenCalledWith(expect.objectContaining({ cone: null, litIds: null }));
});
