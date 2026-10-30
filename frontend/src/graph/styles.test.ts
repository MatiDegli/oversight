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
