import type { StylesheetJson } from "cytoscape";

import type { getActiveTheme } from "../theme";

// Curves separate reciprocal calls. Compare taxi with the final demo layout on Tuesday.
export const EDGE_CURVE_STYLE: "bezier" | "taxi" = "bezier";

type Widen<T> = T extends string
  ? string
  : T extends number
    ? number
    : { [K in keyof T]: Widen<T[K]> };
type GraphTheme = Widen<ReturnType<typeof getActiveTheme>>;

export function graphStyles(theme: GraphTheme): StylesheetJson {
  const px = Number.parseFloat;
  const agents = { A: "agent-a", B: "agent-b" } as const;
  const pendingBorder = {
    "border-style": "dashed" as const,
    "border-width": theme["agent-border-width"],
    "border-dash-pattern": [px(theme["pending-dash-length"]), px(theme["pending-dash-gap"])],
  };
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-family": theme["text-font"],
        "font-size": theme["body-size"],
        "font-weight": theme["body-weight"],
        color: theme.text,
        // Only the explicit newline separates the name from the marks.
        "text-wrap": "wrap",
        "text-max-width": `${String(px(theme["body-size"]) * 1000)}px`,
        "text-valign": "center",
        "background-color": theme.card,
        "background-opacity": theme["node-opacity"],
        "border-color": theme["node-border"],
        "border-width": theme["border-width"],
        "border-style": "solid",
        shape: "round-rectangle",
        "corner-radius": theme["radius-node"],
        // Keep the preset's proportions in units of the graph's body type.
        width: px(theme["body-size"]) * 10,
        height: px(theme["body-size"]) * 4,
      },
    },
    {
      selector: "node.module, node.class",
      style: {
        "text-valign": "top",
        padding: `${String(px(theme["spacing-md"]) * 3)}px`,
        "background-color": theme.panel,
        "background-opacity": theme["module-opacity"],
        "border-color": theme["card-border"],
        "corner-radius": theme["radius-module"],
        color: theme["module-label"],
      },
    },
    ...(["read", "edit"] as const).flatMap((mode): StylesheetJson =>
      (["A", "B"] as const).map((slot) => {
        const agent = agents[slot];
        const fill = theme[`${agent}-node`];
        return {
          selector: `node.focus-${slot}-${mode}`,
          style: {
            "border-color": theme[agent],
            "border-width": theme["agent-border-width"],
            ...(mode === "edit"
              ? {
                  ...pendingBorder,
                  "background-color": fill.color,
                  "background-opacity": fill.alpha,
                }
              : {}),
          },
        };
      }),
    ),
    { selector: "node.pending", style: pendingBorder },
    ...(["A", "B"] as const).flatMap((slot): StylesheetJson => {
      const agent = agents[slot];
      const fill = theme[`${agent}-node`];
      const added = theme[`${agent}-node-new`];
      return [
        {
          selector: `node.pending-${slot}`,
          style: {
            "border-color": theme[agent],
            "background-color": fill.color,
            "background-opacity": fill.alpha,
          },
        },
        {
          selector: `node.pending-new-${slot}`,
          style: { "background-color": added.color, "background-opacity": added.alpha },
        },
        {
          selector: `node.pending-new-${slot}.focus-${slot}-edit`,
          style: { "background-color": fill.color, "background-opacity": fill.alpha },
        },
      ];
    }),
    {
      // Cytoscape has one border color: A takes priority; the label names A+B.
      selector: "node.pending-both",
      style: {
        "border-color": theme["agent-a"],
        "background-color": theme["agent-a-node"].color,
        "background-opacity": theme["agent-a-node"].alpha,
      },
    },
    {
      selector: "node:selected",
      style: { "outline-color": theme.text, "outline-width": theme["selected-ring-width"] },
    },
    {
      selector: "edge",
      style: {
        "curve-style": EDGE_CURVE_STYLE,
        "taxi-direction": "downward",
        "line-style": "solid",
        "line-color": theme.edge,
        "target-arrow-color": theme.edge,
        "target-arrow-shape": "triangle",
        width: theme["exact-edge-width"],
      },
    },
    { selector: "edge.imports", style: { "target-arrow-shape": "chevron" } },
    { selector: "edge.inherits", style: { "target-arrow-shape": "triangle-tee" } },
    ...(["A", "B"] as const).map((slot) => ({
      selector: `edge.pending-${slot}`,
      style: {
        "line-style": "dashed" as const,
        "line-color": theme[agents[slot]],
        "target-arrow-color": theme[agents[slot]],
        width: theme["pending-edge-width"],
        "line-dash-pattern": [px(theme["pending-dash-length"]), px(theme["pending-dash-gap"])],
      },
    })),
    {
      selector: "edge.lit",
      style: {
        "line-color": theme["lit-edge"],
        "target-arrow-color": theme["lit-edge"],
        width: theme["lit-edge-width"],
      },
    },
    {
      selector: "edge.possible",
      style: {
        width: theme["possible-edge-width"],
        "line-style": "solid",
        "line-color": theme["edge-possible"],
        "target-arrow-color": theme["edge-possible"],
      },
    },
    { selector: "node.dimmed, edge.dimmed", style: { opacity: theme["dim-node"] } },
    { selector: "node.module.dimmed", style: { opacity: theme["dim-module"] } },
  ];
}
