import { agentLook, agentSlots } from "../theme";
import type { EdgeSingular, NodeSingular, StylesheetJson } from "cytoscape";
import badgeFont from "@fontsource/atkinson-hyperlegible-next/files/atkinson-hyperlegible-next-latin-700-normal.woff2?inline";

import type { getActiveTheme } from "../theme";

import { cacheLayers } from "./layerCache";
import { layerImages } from "./layerImages";
import { nodeGeometry } from "./geometry";
import { headerLayers, marksClearance, marksLayer } from "./layers";
import type { Band, Layer, NodeMarks } from "./layers";
import type { Route } from "./routes";

export type EdgeShape = "bezier" | "taxi";
// The product default: orthogonal edges routed through the gutters between frames (routes.ts). The
// dev-only query override lets make shots compare identical presets.
export const EDGE_CURVE_STYLE: EdgeShape = "taxi";

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

/**
 * Draw order, low to high: frames, then edges, then leaf nodes. Edges run over a frame's fill but
 * under every node's name and marks. Straight possible links sit below opaque cards and headers.
 */
const LAYER = { frame: 1, edge: 2, leaf: 3 } as const;
const encodedBadgeFonts = new Map<string, string>();

export function graphStyles(
  theme: GraphTheme,
  shape: EdgeShape = EDGE_CURVE_STYLE,
): StylesheetJson {
  const px = Number.parseFloat;
  const badgeSize = px(theme["distance-badge-size"]);
  const badges = new Map<number, string>();
  const badge = (distance: number) => {
    const previous = badges.get(distance);
    if (previous !== undefined) return previous;
    // SVG images cannot see the page's @font-face. Embed the local font for the numeric badge.
    // This decorates the existing node without synthetic graph nodes or layout changes.
    const family = theme["text-font"].split(",")[0] ?? "sans-serif";
    const fontKey = `${family}|${String(theme["distance-badge-font-weight"])}`;
    let encodedFont = encodedBadgeFonts.get(fontKey);
    if (encodedFont === undefined) {
      const font = `<style>@font-face{font-family:${theme["text-font"].split(",")[0] ?? "sans-serif"};src:url("${badgeFont}") format("woff2");font-weight:${String(theme["distance-badge-font-weight"])}}</style>`;
      encodedFont = encodeURIComponent(font);
      encodedBadgeFonts.set(fontKey, encodedFont);
    }
    const open = `<svg xmlns="http://www.w3.org/2000/svg" width="${String(badgeSize)}" height="${String(badgeSize)}">`;
    const body = `<circle cx="${String(badgeSize / 2)}" cy="${String(badgeSize / 2)}" r="${String(badgeSize / 2)}" fill="${theme.text}"/><text x="50%" y="50%" dy=".35em" text-anchor="middle" fill="${theme.page}" font-family='${theme["text-font"]}' font-size="${theme["distance-badge-font-size"]}" font-weight="${String(theme["distance-badge-font-weight"])}">${String(distance)}</text></svg>`;
    const image = `data:image/svg+xml,${encodeURIComponent(open)}${encodedFont}${encodeURIComponent(body)}`;
    badges.set(distance, image);
    return image;
  };
  const geometry = nodeGeometry(theme);
  const distanceBadge = (node: NodeSingular): Layer => ({
    image: badge(node.data("distance") as number),
    width: badgeSize,
    height: badgeSize,
    x: "100%",
    y: "0%",
    offsetX: badgeSize / 2,
    offsetY: -badgeSize / 2,
    clip: "none",
  });
  // A node's outer size, which elements.ts puts in its data: a row's, or a childless frame's.
  const outer = (node: NodeSingular) => ({
    width: (node.data("w") as number | undefined) ?? geometry.rowWidth,
    height: (node.data("h") as number | undefined) ?? geometry.rowHeight,
  });
  // The box Cytoscape gives the node, which it draws a border wider.
  const body = (node: NodeSingular) => {
    const size = outer(node);
    return { width: size.width - geometry.border, height: size.height - geometry.border };
  };
  const marks = (node: NodeSingular): Layer[] => {
    const value = node.data("marks") as NodeMarks | undefined;
    return value ? [marksLayer(theme, value, body(node))] : [];
  };
  const cone = (node: NodeSingular): Layer[] =>
    node.hasClass("cone") ? [distanceBadge(node)] : [];
  const frameText = (node: NodeSingular) =>
    [String(node.data("title") ?? ""), String(node.data("count") ?? "")] as const;
  // A frame with children: its header row, which carries the agent's state word if one touches it.
  // The row spans the frame's outer width: `width()` is its content, without the padding around it.
  const frameLayers = (node: NodeSingular): Layer[] => [
    ...headerLayers(
      theme,
      ...frameText(node),
      { width: node.outerWidth(), height: node.outerHeight() },
      node.hasClass("class") ? "class" : "module",
      node.data("marks") as NodeMarks | undefined,
    ),
    ...cone(node),
  ];
  // Everything without children: a row's state word, a childless frame's single header row, and the
  // distance badge when it is in the cone.
  const leafLayers = (node: NodeSingular): Layer[] => [
    ...(node.hasClass("module") || node.hasClass("class")
      ? headerLayers(
          theme,
          ...frameText(node),
          body(node),
          "empty" satisfies Band,
          node.data("marks") as NodeMarks | undefined,
          node.hasClass("folded"),
        )
      : marks(node)),
    ...cone(node),
  ];
  // What the name may take of a row: its width less both paddings, and less the state word and
  // badges when an agent touches it. Cytoscape cuts the name with an ellipsis at this width.
  const nameRoom = (node: NodeSingular) => {
    const value = node.data("marks") as NodeMarks | undefined;
    const taken = value ? marksClearance(theme, value) + px(theme["node-word-gap"]) : 0;
    return Math.max(
      0,
      outer(node).width - px(theme["node-pad-left"]) - px(theme["node-pad-right"]) - taken,
    );
  };
  const frameOnce = cacheLayers(frameLayers, true);
  const leafOnce = cacheLayers(leafLayers, false);
  const shortImage = layerImages();
  // One property per image: Cytoscape takes a list for each when a node carries several.
  const layered = (layers: (node: NodeSingular) => Layer[]) => ({
    "background-image": (node: NodeSingular) =>
      layers(node).map((layer) => shortImage(node, layer.image)),
    "background-width": (node: NodeSingular) => layers(node).map((layer) => layer.width),
    "background-height": (node: NodeSingular) => layers(node).map((layer) => layer.height),
    "background-position-x": (node: NodeSingular) => layers(node).map((layer) => layer.x),
    "background-position-y": (node: NodeSingular) => layers(node).map((layer) => layer.y),
    "background-offset-x": (node: NodeSingular) => layers(node).map((layer) => layer.offsetX),
    "background-offset-y": (node: NodeSingular) => layers(node).map((layer) => layer.offsetY),
    "background-clip": (node: NodeSingular) => layers(node).map((layer) => layer.clip),
    "background-image-containment": (node: NodeSingular) =>
      layers(node).map((layer) => (layer.clip === "none" ? "over" : "inside")),
  });
  const minimum = (key: "minW" | "minH") => (node: NodeSingular) =>
    `${String((node.data(key) as number | undefined) ?? 0)}px`;
  const routeOf = (edge: EdgeSingular) => edge.data("route") as Route;
  const endpoint = (edge: EdgeSingular, end: "s" | "t") => {
    const [x, y] = routeOf(edge)[end];
    return `${String(x)}px ${String(y)}px`;
  };
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
        "font-size": theme["node-name-size"],
        "font-weight": theme["node-name-weight"],
        color: theme.text,
        // The name is the whole label; the state word and badges are images beside it, so the name
        // is cut with an ellipsis where it would meet them.
        "text-wrap": "ellipsis",
        "text-max-width": (node: NodeSingular) => `${String(nameRoom(node))}px`,
        "text-valign": "center",
        "background-color": theme.card,
        "background-opacity": theme["node-opacity"],
        "border-color": theme["node-border"],
        "border-width": theme["border-width"],
        "border-style": "solid",
        shape: "round-rectangle",
        "corner-radius": theme["radius-node"],
        // A row, or a childless frame, in the size elements.ts gives it; a parent takes its
        // children's.
        width: (node: NodeSingular) => body(node).width,
        height: (node: NodeSingular) => body(node).height,
        "z-index-compare": "manual",
        "z-index": LAYER.leaf,
      },
    },
    {
      // A row's name starts at the row's left padding. The label sits outside the box's right
      // edge, so a negative margin of the box's width, less that padding, brings it in.
      selector: "node:childless",
      style: {
        "text-halign": "right",
        "text-margin-x": (node: NodeSingular) =>
          -(body(node).width - (px(theme["node-pad-left"]) - geometry.border / 2)),
      },
    },
    {
      // A module or class keeps its frame colors with or without children, so it still reads as a
      // container; only a parent gets the padding and the header band above its first row.
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
      // A folded card masks links behind its text; an open body's fill lets the field through.
      selector: "node.module:childless",
      style: { "background-opacity": 1 },
    },
    {
      selector: "node.module:parent, node.class:parent",
      style: {
        "text-valign": "top",
        // Rows carry their own names: a frame's bounds are its children's boxes and its padding.
        "compound-sizing-wrt-labels": "exclude",
        "z-index": LAYER.frame,
      },
    },
    {
      // Even padding by the smallest side; frames.ts asks for the rest as a minimum size, with the
      // extra at the top (the header band) and, for a module, at the left (the lane for edges).
      selector: "node.module:parent, node.class:parent",
      style: {
        "min-width": minimum("minW"),
        "min-height": minimum("minH"),
        "min-width-bias-left": "100%",
        "min-width-bias-right": "0%",
        "min-height-bias-top": "100%",
        // Cytoscape reads this; its type definitions leave it out.
        ...({ "min-height-bias-bottom": "0%" } as object),
      },
    },
    {
      selector: "node.module:parent",
      style: { padding: `${String(geometry.module.bottom)}px` },
    },
    {
      selector: "node.class:parent",
      style: { padding: `${String(geometry.class.bottom)}px` },
    },
    {
      // A frame's title and count are its header row, drawn as images; its label would sit above it.
      selector: "node:parent",
      style: { label: "", ...layered(frameOnce) },
    },
    {
      // An empty module or class is a box of one header row; it wears its frame's title and count.
      selector: "node.module:childless, node.class:childless",
      style: { label: "", ...layered(leafOnce) },
    },
    {
      selector: "node.marked:childless",
      style: layered(leafOnce),
    },
    ...(["read", "edit"] as const).flatMap((mode): StylesheetJson =>
      agentSlots.map((slot) => {
        const agent = agentLook[slot].family;
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
    ...agentSlots.flatMap((slot): StylesheetJson => {
      const agent = agentLook[slot].family;
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
      // The distance badge hangs over the node's top-right corner, outside its box. A node that
      // also carries other images gets the badge in its own layered list (frameLayers, leafLayers).
      selector: "node.cone",
      style: {
        "bounds-expansion": badgeSize / 2,
      },
    },
    {
      selector: "node.cone:childless",
      style: layered(leafOnce),
    },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        "line-style": "solid",
        "line-color": theme.edge,
        "target-arrow-color": theme.edge,
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.8,
        width: theme["exact-edge-width"],
        "z-index-compare": "manual",
        "z-index": LAYER.edge,
      },
    },
    // Orthogonal edges follow the route routes.ts computed and stored in the edge's data: each end
    // a fixed point on its node's border, and the bends placed along the line between them.
    ...(shape === "taxi"
      ? [
          {
            selector: "edge[route]",
            style: {
              "curve-style": "round-segments" as const,
              // Cytoscape accepts "endpoints" (distances along the line between the manual
              // endpoints); its type definitions list only the other two.
              "edge-distances": "endpoints" as unknown as "intersection",
              "source-endpoint": (edge: EdgeSingular) => endpoint(edge, "s"),
              "target-endpoint": (edge: EdgeSingular) => endpoint(edge, "t"),
              "segment-weights": (edge: EdgeSingular) => routeOf(edge).w,
              "segment-distances": (edge: EdgeSingular) => routeOf(edge).d,
              "segment-radii": [px(theme["edge-radius"])],
            },
          },
        ]
      : []),
    // The sketch draws calls only: a module's imports say which files it uses, not which functions
    // a change may reach, so they stay in the graph's data and off its picture.
    {
      selector: "edge.imports, edge.internal, edge.unroutable",
      style: { "target-arrow-shape": "chevron", display: "none" },
    },
    { selector: "edge.inherits", style: { "target-arrow-shape": "triangle-tee" } },
    ...agentSlots.map((slot) => ({
      selector: `edge.pending-${slot}`,
      style: {
        "line-style": "dashed" as const,
        "line-color": theme[agentLook[slot].family],
        "target-arrow-color": theme[agentLook[slot].family],
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
        display: "none",
        "curve-style": "straight",
        "source-endpoint": "outside-to-node",
        "target-endpoint": "outside-to-node",
        "target-arrow-shape": "none",
        "z-compound-depth": "bottom",
        "z-index": 0,
        width: theme["possible-edge-width"],
        "line-style": "solid",
        "line-color": theme["edge-possible"],
        "target-arrow-color": theme["edge-possible"],
      },
    },
    {
      selector: "edge.possible.faint",
      style: { display: "element", width: 1, "line-color": theme["edge-faint"], opacity: 1 },
    },
    // Watch mode: an edge no agent touches steps back; one at a node the person selected comes back.
    { selector: "edge.quiet", style: { opacity: theme["quiet-edge"] } },
    { selector: "edge.touching, edge.node-hover", style: { opacity: 1 } },
    {
      selector: "edge.possible.touching, edge.possible.node-hover, edge.possible.cone-call",
      style: {
        display: "element",
        width: theme["possible-edge-width"],
        "line-color": theme["edge-possible"],
        "target-arrow-shape": "triangle",
        opacity: 1,
      },
    },
    {
      selector: "edge.aggregate",
      style: {
        label: "",
        "font-family": theme["text-font"],
        "font-size": theme["frame-count-size"],
        color: theme.muted,
        "text-background-color": theme.ground,
        "text-background-opacity": 1,
        "text-background-padding": theme["spacing-xs"],
        "text-background-shape": "roundrectangle",
      },
    },
    {
      selector: "edge.aggregate.count-hover, edge.aggregate.touching",
      style: {
        label: (edge: EdgeSingular) => {
          const exact = edge.data("exactCount") as number | undefined;
          const possible = edge.data("possibleCount") as number | undefined;
          return exact === undefined
            ? `×${String(edge.data("callCount"))}`
            : exact
              ? `×${String(exact)}${possible ? ` · ${String(possible)} possible` : ""}`
              : `×${String(possible)} possible`;
        },
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
        "background-image-opacity": theme["dim-node"],
        "border-opacity": theme["dim-node"],
        "text-opacity": theme["dim-node"],
      },
    },
    {
      selector: "node.module:parent.dimmed",
      style: {
        "background-opacity": theme["module-opacity"] * theme["dim-module"],
        "background-image-opacity": theme["dim-module"],
        "border-opacity": theme["dim-module"],
        "text-opacity": theme["dim-module"],
      },
    },
    {
      selector: "node.module.folded, node.module.edge-shield",
      style: { "background-opacity": 1, "z-index": 5, "z-compound-depth": "top" },
    },
    {
      selector: "edge.possible.count-hover",
      style: { "target-arrow-shape": "triangle" },
    },
    {
      selector: "edge.count-hover",
      style: {
        "line-color": theme["edge-hover"],
        "target-arrow-color": theme["edge-hover"],
        "z-index": LAYER.leaf + 1,
        "z-compound-depth": "top",
        opacity: 1,
      },
    },
  ];
}
