import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import cytoscape from "cytoscape";
import { expect, it } from "vitest";
import { createInitialState } from "../state";
import { themes } from "../theme";
import { graphElements } from "./elements";
import { readBoltonsSnapshot } from "./exactFixtures.testSupport";
import { foldElements } from "./foldElements";
import { applyRoutes } from "./routes";
import { showNodeHover, showSelection } from "./selection";
import { graphStyles } from "./styles";

const root = new URL("../../../", import.meta.url);
const boltons = readBoltonsSnapshot(root);
const events = readFileSync(new URL("fixtures/events/recorded-demo.jsonl", root), "utf8")
  .trim()
  .split("\n")
  .map((s) => JSON.parse(s) as ServerEvent);
const snapshot = events.find((e) => e.type === "graph.snapshot");
if (snapshot?.type !== "graph.snapshot") throw new Error("Missing demo snapshot");
const demo = snapshot.payload;

it("keeps internal exact calls hidden during hover, selection and cone or agent lighting", () => {
  const full = graphElements({ ...createInitialState(), graph: demo });
  const projected = foldElements(
    full,
    new Set(full.filter((e) => e.data.kind === "class").map((e) => e.data.id)),
    new Set(),
  );
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    style: graphStyles(themes.dark),
    elements: projected.elements,
    layout: { name: "preset" },
  });
  try {
    const edges = cy.edges(".internal.exact");
    expect(edges.length).toBeGreaterThan(0);
    for (const edge of edges) {
      expect(edge.style("display")).toBe("none");
      for (const show of [showNodeHover, showSelection]) {
        for (const id of [edge.source().id(), edge.source().ancestors(".module").first().id()]) {
          show(cy, id);
          expect(edge.hasClass(show === showSelection ? "touching" : "node-hover")).toBe(true);
          expect(edge.style("display")).toBe("none");
          show(cy, null);
        }
      }
      edge.addClass("lit");
      expect(edge.style("display")).toBe("none");
      edge.removeClass("lit quiet").addClass("pending-A");
      expect(edge.style("display")).toBe("none");
    }
  } finally {
    cy.destroy();
  }
});

for (const [name, graph, exactPairs, faint, exactCalls] of [
  ["Boltons", boltons, 13, 9, 13],
  ["demo", demo, 4, 3, 12],
] as const) {
  if (graph === null) {
    // Only the optional external fixture may skip; the recorded demo always runs.
    it.skip(`${name}: exact-only directed counts, one faint line per surviving unordered pair`);
    it.skip(
      `${name}: open calls stay exact at rest, possible calls reveal and clear without agent revival`,
    );
    continue;
  }
  const state = { ...createInitialState(), graph };
  const full = graphElements(state);
  const modules = new Set(full.filter((e) => e.data.kind === "module").map((e) => e.data.id));
  const classes = new Set(full.filter((e) => e.data.kind === "class").map((e) => e.data.id));
  const mount = (open: boolean, input = full, expanded = classes) => {
    const projected = foldElements(input, expanded, open ? new Set() : modules);
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      style: graphStyles(themes.dark),
      elements: projected.elements,
      layout: { name: "preset" },
    });
    cy.scratch("foldAliases", projected.visible);
    applyRoutes(cy, themes.dark);
    return cy;
  };
  // Large headless Boltons mounts need room under parallel checks; demo keeps Vitest's default.
  const timeout = name === "Boltons" ? 20_000 : undefined;
  it(
    `${name}: exact-only directed counts, one faint line per surviving unordered pair`,
    () => {
      const cy = mount(false);
      try {
        expect(cy.edges(".exact").filter((e) => e.visible())).toHaveLength(exactPairs);
        expect(cy.edges(".possible").filter((e) => e.visible())).toHaveLength(faint);
        const pairs = new Set(
          cy.edges(".faint").map((e) => [e.source().id(), e.target().id()].sort().join(">")),
        );
        expect(pairs.size).toBe(faint);
        for (const e of cy.edges(".aggregate")) {
          expect(e.data("callCount")).toBe(e.data("exactCount") || e.data("possibleCount"));
          expect(e.hasClass("exact") && e.hasClass("possible")).toBe(false);
        }
        if (name === "Boltons") {
          expect(cy.nodes()).toHaveLength(34);
          expect(
            cy.nodes().filter((n) =>
              n
                .connectedEdges()
                .filter((e) => e.visible())
                .empty(),
            ),
          ).toHaveLength(16);
          expect(
            cy
              .edges(".aggregate")
              .reduce(
                (n, e) => n + Number(e.data("exactCount")) + Number(e.data("possibleCount")),
                0,
              ),
          ).toBe(1530);
        }
        const hidden = cy
          .edges(".possible")
          .filter((e) => !e.visible())
          .first();
        if (hidden.isEdge()) {
          showSelection(cy, hidden.source().id());
          expect(hidden.style("display")).toBe("element");
          showSelection(cy, null);
          expect(hidden.style("display")).toBe("none");
        }
      } finally {
        cy.destroy();
      }
    },
    timeout,
  );
  it(
    `${name}: open calls stay exact at rest, possible calls reveal and clear without agent revival`,
    () => {
      const functions = mount(true, full, new Set());
      expect(
        functions
          .edges(".exact")
          .not(".internal")
          .filter((e) => e.visible()),
      ).toHaveLength(name === "demo" ? 10 : exactCalls);
      expect(functions.edges(".possible").filter((e) => e.visible())).toHaveLength(0);
      functions.destroy();
      const cy = mount(true);
      try {
        expect(
          cy
            .edges(".exact")
            .not(".internal")
            .filter((e) => e.visible()),
        ).toHaveLength(exactCalls);
        expect(cy.edges(".possible").filter((e) => e.visible())).toHaveLength(0);
        const edge = cy.edges(".possible").not(".internal").toArray()[0];
        if (!edge?.isEdge()) throw new Error("Missing possible edge");
        const source = edge.source();
        for (const show of [showNodeHover, showSelection]) {
          for (const id of [source.id(), source.ancestors(".module").first().id()]) {
            show(cy, id);
            expect(edge.style("display")).toBe("element");
            show(cy, null);
            expect(edge.style("display")).toBe("none");
          }
        }
        edge.removeClass("quiet").addClass("pending-A");
        expect(edge.style("display")).toBe("none");
        edge.addClass("cone-call");
        expect(edge.style("display")).toBe("element");
        expect(edge.style("line-style")).toBe("solid");
        edge.removeClass("cone-call");
        expect(edge.style("display")).toBe("none");
      } finally {
        cy.destroy();
      }
      const possible = graph.edges.find(
        (e) =>
          e.confidence === "possible" &&
          e.kind === "calls" &&
          full.some((n) => n.data.id === e.src) &&
          full.some((n) => n.data.id === e.dst),
      );
      if (!possible) throw new Error("Missing possible call");
      const shown = graphElements(state, false, new Set([possible.src, possible.dst]), {
        seeds: [possible.src],
        entries: [{ node_id: possible.dst, distance: 1 }],
        edges_followed: "exact",
        max_depth: 3,
        max_nodes: 40,
        omitted_by_depth: 0,
        omitted_by_limit: 0,
        tests_reaching: 0,
      });
      expect(shown.find((e) => e.data.id === possible.id)?.classes).toContain("cone-call");
    },
    timeout,
  );
}
