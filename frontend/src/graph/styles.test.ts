import cytoscape from "cytoscape";
import { expect, it } from "vitest";

import { themes } from "../theme";
import { graphStyles } from "./styles";

it.each(["dark", "day"] as const)(
  "%s keeps folded cards opaque and restores the frame token when opened",
  (name) => {
    const theme = themes[name];
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      layout: { name: "preset" },
      style: graphStyles(theme),
      elements: [
        { data: { id: "module", title: "Module", count: "1 function" }, classes: "module folded" },
      ],
    });
    try {
      const module = cy.getElementById("module");
      expect(module.numericStyle("background-opacity")).toBe(1);
      const row = cy.add({
        data: { id: "row", parent: "module", label: "Function" },
        classes: "function",
      });
      module.removeClass("folded");
      expect(module.numericStyle("background-opacity")).toBe(theme["module-opacity"]);
      expect(row.numericStyle("background-opacity")).toBe(theme["node-opacity"]);
      // Returning to Modules must still mask the straight possible links behind the card's text.
      row.remove();
      module.addClass("folded");
      expect(module.numericStyle("background-opacity")).toBe(1);
    } finally {
      cy.destroy();
    }
  },
);

it.each([
  ["dark", false],
  ["dark", true],
  ["day", false],
  ["day", true],
] as const)("%s possible-edge hover preserves open frames (review: %s)", (name, review) => {
  const theme = themes[name];
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    style: graphStyles(theme),
    elements: [
      { data: { id: "module" }, classes: `module${review ? " dimmed" : ""}` },
      { data: { id: "row", parent: "module", label: "Function" }, classes: "function" },
      { data: { id: "other" }, classes: "module" },
      { data: { id: "other-row", parent: "other", label: "Caller" }, classes: "function" },
      { data: { id: "folded", title: "Folded", count: "1 function" }, classes: "module folded" },
      {
        data: { id: "possible", source: "row", target: "other-row" },
        classes: "call possible touching",
      },
    ],
  });
  try {
    const frames = cy.nodes(".module:parent");
    const before = frames.map((frame) => ({
      frame,
      background: Number(frame.numericStyle("background-opacity")),
      depth: String(frame.style("z-compound-depth")),
    }));
    const edge = cy.getElementById("possible");
    expect(edge.style("display")).toBe("element");
    // Match GraphView's enter/leave classes, including the shield on unrelated modules.
    edge.addClass("count-hover");
    cy.nodes(".module").addClass("edge-shield");
    before.forEach(({ frame, background, depth }) => {
      expect(frame.numericStyle("z-index")).toBe(1);
      expect(frame.style("z-compound-depth")).toBe(depth);
      expect(frame.numericStyle("background-opacity")).toBe(background);
      expect(frame.children().first().numericStyle("z-index")).toBe(3);
    });
    expect(cy.getElementById("module").numericStyle("background-opacity")).toBe(
      theme["module-opacity"] * (review ? theme["dim-module"] : 1),
    );
    expect(edge.numericStyle("z-index")).toBe(4);
    expect(cy.getElementById("folded").numericStyle("z-index")).toBe(5);
    expect(cy.getElementById("folded").numericStyle("background-opacity")).toBe(1);
    edge.removeClass("count-hover");
    cy.nodes(".module").removeClass("edge-shield");
    before.forEach(({ frame, background }) => {
      expect(frame.numericStyle("z-index")).toBe(1);
      expect(frame.numericStyle("background-opacity")).toBe(background);
    });
    expect(edge.numericStyle("z-index")).toBe(0);
  } finally {
    cy.destroy();
  }
});
