import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { ImpactConeCard, impactSummary } from "./ImpactCone";
import { ReviewPanel } from "./ReviewPanel";

vi.mock("../editor", () => ({ EditorDiff: () => <div>Inline diff</div> }));

const state = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent)
  .filter((event) => event.seq <= 20)
  .reduce(applyEvent, createInitialState());
const proposal = state.changes["change-1"]?.proposal;
if (!proposal) throw new Error("Missing recorded proposal");
const cone = proposal.impact;
const nodes = state.graph?.nodes ?? [];

describe("the recorded impact cone", () => {
  it("groups the handwritten entries by distance with counts, neutral badges and names", () => {
    const html = renderToStaticMarkup(<ImpactConeCard cone={cone} nodes={nodes} />);
    expect(html).toContain("Possibly affected");
    expect(html).toContain("Distance 1 · 7 functions");
    expect(html).toContain("Distance 2 · 2 functions");
    expect(html.match(/<li /g)).toHaveLength(9);
    expect(html.match(/<span aria-label="Distance /g)).toHaveLength(9);
    for (const entry of cone.entries) {
      const node = nodes.find((node) => node.id === entry.node_id);
      expect(html).toContain(node?.qualname);
    }
    expect(html).not.toContain("omitted");
    expect(html).not.toContain("agent-a");
    expect(html).not.toContain("agent-b");
    expect(html).not.toContain("Out of view");
    expect(html).toContain("size-(--distance-badge-size)");
  });

  it("marks only the camera's excluded nodes with an icon and screen-reader-visible text", () => {
    const excluded = cone.entries[0]?.node_id;
    if (!excluded) throw new Error("Missing caller");
    const html = renderToStaticMarkup(
      <ImpactConeCard cone={cone} nodes={nodes} outOfView={new Set([excluded])} />,
    );
    const rows = html.match(/<li .*?<\/li>/g) ?? [];
    expect(rows).toHaveLength(cone.entries.length);
    for (const row of rows) {
      expect(row.includes("Out of view")).toBe(row.includes(`title="${excluded}"`));
      if (row.includes("Out of view")) {
        expect(row).toContain("<svg");
        expect(row).toContain('aria-hidden="true"');
        expect(row).not.toContain('class="sr-only"');
      }
    }
  });

  it("makes only a row that says Out of view a button that reveals it, and only given a handler", () => {
    const away = cone.entries[0]?.node_id;
    if (!away) throw new Error("Missing caller");
    const rows = (html: string) => html.match(/<li .*?<\/li>/g) ?? [];
    const withHandler = renderToStaticMarkup(
      <ImpactConeCard
        cone={cone}
        nodes={nodes}
        outOfView={new Set([away])}
        onReveal={() => undefined}
      />,
    );
    expect(withHandler.match(/<button /g)).toHaveLength(1);
    for (const row of rows(withHandler)) {
      expect(row.includes("<button ")).toBe(row.includes(`title="${away}"`));
      if (row.includes("<button ")) {
        expect(row).toContain('type="button"');
        expect(row).toContain("Out of view");
      }
    }
    // No handler, or nothing out of view: the card stays plain text.
    const without = renderToStaticMarkup(
      <ImpactConeCard cone={cone} nodes={nodes} outOfView={new Set([away])} />,
    );
    const inView = renderToStaticMarkup(
      <ImpactConeCard cone={cone} nodes={nodes} onReveal={() => undefined} />,
    );
    expect(without).not.toContain("<button");
    expect(inView).not.toContain("<button");
  });

  it("shows each nonzero cut independently and preserves unknown entries by id", () => {
    for (const [depth, limit] of [
      [12, 3],
      [12, 0],
      [0, 3],
    ]) {
      const html = renderToStaticMarkup(
        <ImpactConeCard
          cone={{ ...cone, omitted_by_depth: depth ?? 0, omitted_by_limit: limit ?? 0 }}
          nodes={[]}
        />,
      );
      expect(html.includes("+12 omitted by depth")).toBe(Boolean(depth));
      expect(html.includes("+3 omitted by the node limit")).toBe(Boolean(limit));
      expect(html).toContain(cone.entries[0]?.node_id);
    }
  });

  it("states an empty cone plainly without implying no impact beyond the bounds", () => {
    const empty = { ...cone, entries: [], omitted_by_depth: 12, omitted_by_limit: 3 };
    const html = renderToStaticMarkup(<ImpactConeCard cone={empty} nodes={nodes} />);
    expect(html).toContain("No functions in this cone within its bounds.");
    expect(html).toContain("+12 omitted by depth");
    expect(html).toContain("+3 omitted by the node limit");
    expect(html).not.toContain('aria-label="Distance');
    expect(impactSummary(empty)).toBe("No functions possibly affected within the cone bounds");
  });

  it("renders decision errors in a neutral token with an icon", () => {
    const html = renderToStaticMarkup(
      <ReviewPanel
        state={state}
        changeId="change-1"
        attempt={{ phase: "failed", action: "approve", revision: 1, message: "Busy. Try again." }}
        onDecide={() => undefined}
        onClose={() => undefined}
      />,
    );
    const alert = html.match(/<p role="alert".*?<\/p>/)?.[0];
    expect(alert).toContain("text-text");
    expect(alert).not.toContain("text-failed-text");
    expect(alert).toContain("<svg");
    expect(alert).toContain('aria-hidden="true"');
    expect(alert).toContain("Busy. Try again.");
  });

  it("summarizes the displayed functions and farthest distance, including singular", () => {
    expect(impactSummary(cone)).toBe("9 functions possibly affected, up to distance 2");
    expect(impactSummary({ ...cone, entries: [{ node_id: "caller", distance: 3 }] })).toBe(
      "1 function possibly affected, up to distance 3",
    );
    const html = renderToStaticMarkup(
      <ReviewPanel state={state} changeId="change-1" onClose={() => undefined} />,
    );
    expect(html).toContain('class="text-(length:--data-size) text-muted"');
    expect(html).toContain("9 functions possibly affected, up to distance 2");
  });
});
