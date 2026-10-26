import type { NodeSingular, StylesheetJson } from "cytoscape";

import { agentColors, colors, fontFamilies } from "../theme";

// Curves separate reciprocal calls. Compare taxi with the final demo layout on Tuesday.
export const EDGE_CURVE_STYLE: "bezier" | "taxi" = "bezier";

const numeric = (key: string) => (node: NodeSingular) => Number(node.data(key));

export const graphStyles: StylesheetJson = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "font-family": fontFamilies.sans,
      "font-size": 14,
      color: colors.text,
      "text-wrap": "wrap",
      "text-valign": "center",
      "background-color": colors.panel,
      "border-color": colors.border,
      "border-width": 1,
      "border-style": "solid",
      shape: "round-rectangle",
      width: 150,
      height: 60,
      "pie-1-background-color": agentColors.A,
      "pie-1-background-size": numeric("aSize"),
      "pie-1-background-opacity": numeric("aOpacity"),
      "pie-2-background-color": agentColors.B,
      "pie-2-background-size": numeric("bSize"),
      "pie-2-background-opacity": numeric("bOpacity"),
    },
  },
  { selector: "node.module, node.class", style: { "text-valign": "top", padding: "30px" } },
  { selector: "node.method", style: { shape: "ellipse" } },
  {
    selector: "node.pending",
    style: { "border-style": "dashed", "border-width": 3, "border-color": "data(pendingColor)" },
  },
  {
    selector: "edge",
    style: {
      "curve-style": EDGE_CURVE_STYLE,
      "taxi-direction": "downward",
      "line-style": "solid",
      "line-color": colors.border,
      "target-arrow-color": colors.border,
      "target-arrow-shape": "triangle",
      width: 2,
    },
  },
  { selector: "edge.imports", style: { "target-arrow-shape": "chevron" } },
  { selector: "edge.inherits", style: { "target-arrow-shape": "triangle-tee" } },
  {
    selector: "edge.possible",
    style: { width: 1, "line-color": colors.muted, "target-arrow-color": colors.muted },
  },
];
