import type { StylesheetJson } from "cytoscape";

import type * as themeTokens from "../theme";

// Curves separate reciprocal calls. Compare taxi with the final demo layout on Tuesday.
export const EDGE_CURVE_STYLE: "bezier" | "taxi" = "bezier";

type GraphTheme = {
  [Group in "agentColors" | "colors" | "fontFamilies"]: {
    [Token in keyof (typeof themeTokens)[Group]]: string;
  };
};

export function graphStyles({ agentColors, colors, fontFamilies }: GraphTheme): StylesheetJson {
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-family": fontFamilies.sans,
        "font-size": 14,
        color: colors.text,
        "text-wrap": "ellipsis",
        "text-max-width": "140px",
        "text-valign": "center",
        "background-color": colors.panel,
        "border-color": colors.border,
        "border-width": 1,
        "border-style": "solid",
        shape: "round-rectangle",
        width: 150,
        height: 60,
      },
    },
    { selector: "node.module, node.class", style: { "text-valign": "top", padding: "30px" } },
    { selector: "node.method", style: { shape: "ellipse" } },
    ...(["A", "B"] as const).flatMap((slot): StylesheetJson => [
      {
        selector: `node.focus-${slot}-read, node.focus-${slot}-edit`,
        style: { "background-color": agentColors[slot], "border-color": agentColors[slot] },
      },
      {
        selector: `node.focus-${slot}-read`,
        style: { "background-opacity": 0.16, "border-opacity": 0.65, "border-width": 2 },
      },
      {
        selector: `node.focus-${slot}-edit`,
        style: { "background-opacity": 0.35, "border-opacity": 1, "border-width": 3 },
      },
    ]),
    {
      selector: "node.focus-both, node.pending-both",
      style: {
        "background-fill": "linear-gradient",
        "background-gradient-direction": "to-right",
        "background-gradient-stop-colors": [
          agentColors.A,
          agentColors.A,
          agentColors.B,
          agentColors.B,
        ],
        "background-gradient-stop-positions": [0, 50, 50, 100],
        "background-opacity": 0.16,
      },
    },
    {
      selector: "node.focus-A-edit, node.focus-B-edit",
      style: { "background-opacity": 0.35, "border-opacity": 1, "border-width": 3 },
    },
    {
      selector: "node.pending",
      style: { "border-style": "dashed", "border-width": 3, "border-opacity": 1 },
    },
    ...(["A", "B"] as const).map((slot) => ({
      selector: `node.pending-${slot}`,
      style: { "border-color": agentColors[slot] },
    })),
    {
      selector: "edge",
      style: {
        "curve-style": EDGE_CURVE_STYLE,
        "taxi-direction": "downward",
        "line-style": "solid",
        "line-color": colors.muted,
        "target-arrow-color": colors.muted,
        "target-arrow-shape": "triangle",
        width: 2,
      },
    },
    { selector: "edge.imports", style: { "target-arrow-shape": "chevron" } },
    { selector: "edge.inherits", style: { "target-arrow-shape": "triangle-tee" } },
    {
      selector: "edge.possible",
      style: { width: 1, "line-color": colors.border, "target-arrow-color": colors.border },
    },
  ];
}
