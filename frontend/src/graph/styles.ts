import type { StylesheetJson } from "cytoscape";
import badgeFont from "@fontsource/atkinson-hyperlegible-next/files/atkinson-hyperlegible-next-latin-700-normal.woff2?inline";

import type { getActiveTheme } from "../theme";

import { nodeGeometry } from "./geometry";

export type EdgeShape = "bezier" | "taxi";
// The product default. The dev-only query override lets make shots compare identical presets.
export const EDGE_CURVE_STYLE: EdgeShape = "bezier";

export function edgeShape(search = ""): EdgeShape {
  const requested = new URLSearchParams(search).get("edge-shape");
  return requested === "taxi" || requested === "bezier" ? requested : EDGE_CURVE_STYLE;
}

type Widen<T> = T extends string
  ? string
  : T extends number
    ? number
    : { [K in keyof T]: Widen<T[K]> };
type GraphTheme = Widen<ReturnType<typeof getActiveTheme>>;

export function graphStyles(
  theme: GraphTheme,
  shape: EdgeShape = EDGE_CURVE_STYLE,
): StylesheetJson {
  const px = Number.parseFloat;
  const agents = { A: "agent-a", B: "agent-b" } as const;
  const badgeSize = px(theme["distance-badge-size"]);
  const badge = (distance: number) => {
    // SVG images cannot see the page's @font-face. Embed the local font for the numeric badge.
    // This decorates the existing node without synthetic graph nodes or layout changes.
    const font = `<style>@font-face{font-family:${theme["text-font"].split(",")[0] ?? "sans-serif"};src:url("${badgeFont}") format("woff2");font-weight:${String(theme["distance-badge-font-weight"])}}</style>`;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${String(badgeSize)}" height="${String(badgeSize)}">${font}<circle cx="${String(badgeSize / 2)}" cy="${String(badgeSize / 2)}" r="${String(badgeSize / 2)}" fill="${theme.text}"/><text x="50%" y="50%" dy=".35em" text-anchor="middle" fill="${theme.page}" font-family='${theme["text-font"]}' font-size="${theme["distance-badge-font-size"]}" font-weight="${String(theme["distance-badge-font-weight"])}">${String(distance)}</text></svg>`;
    return `data:image/svg+xml,${encodeURIComponent(svg)}`;
  };
  const geometry = nodeGeometry(theme);
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
        width: geometry.width,
        height: geometry.height,
      },
    },
    {
      // A module or class keeps its frame colors with or without children, so it still reads as a
      // container; only a parent gets the padding and the label above it.
      selector: "node.module, node.class",
      style: {
        "background-color": theme.panel,
        "background-opacity": theme["module-opacity"],
        "border-color": theme["card-border"],
        "corner-radius": theme["radius-module"],
        color: theme["module-label"],
      },
    },
    {
      selector: "node.module:parent, node.class:parent",
      style: { "text-valign": "top", padding: `${String(geometry.padding)}px` },
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
      selector: "node.cone",
      style: {
        "background-image": (node) => badge(node.data("distance") as number),
        "background-width": theme["distance-badge-size"],
        "background-height": theme["distance-badge-size"],
        "background-position-x": "100%",
        "background-position-y": "0%",
        "background-offset-x": badgeSize / 2,
        "background-offset-y": -badgeSize / 2,
        "background-image-containment": "over",
        "background-clip": "none",
        "bounds-expansion": badgeSize / 2,
      },
    },
    {
      selector: "edge",
      style: {
        "curve-style": shape,
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
    {
      // Overall compound opacity multiplies into every child. Dim only the frame's paint.
      selector: "node:parent.dimmed",
      style: {
        opacity: 1,
        "background-opacity": theme["module-opacity"] * theme["dim-node"],
        "border-opacity": theme["dim-node"],
        "text-opacity": theme["dim-node"],
      },
    },
    {
      selector: "node.module:parent.dimmed",
      style: {
        "background-opacity": theme["module-opacity"] * theme["dim-module"],
        "border-opacity": theme["dim-module"],
        "text-opacity": theme["dim-module"],
      },
    },
  ];
}
