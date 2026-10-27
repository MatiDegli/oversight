// Sizes the graph draws and the preset reserves, derived from theme tokens in one place so the
// stylesheet (styles.ts), the placement of new nodes (updateGraph.ts), the edge router (routes.ts)
// and the preset generator (tools/layout.ts) cannot drift apart. This file imports nothing at run
// time: the generator runs it under plain Node.
//
// The numbers are the Signal sketch's, measured at 100% on a 1920x1080 frame: a function or method
// is a row of 282x46 px, rows stack in one column inside their module's frame, 11 px apart, and a
// frame has a header band above its first row. A class is a frame inside the module's, holding its
// own rows; it is as wide as a row of the module, so its rows are narrower by its side padding.

/** The tokens the geometry reads; a full theme satisfies it. */
export interface GeometryTokens {
  "node-width": string;
  "node-height": string;
  "node-gap": string;
  "node-name-size": string;
  "spacing-md": string;
  "border-width": string;
  "frame-header-height": string;
  "frame-pad-top": string;
  "frame-pad-left": string;
  "frame-pad-right": string;
  "frame-pad-bottom": string;
  "class-pad-top": string;
  "class-pad-x": string;
  "class-pad-bottom": string;
  "frame-gutter": string;
  "frame-row-gap": string;
}

/** The padding Cytoscape puts between a frame's children and its border, side by side. */
export interface Padding {
  top: number;
  left: number;
  right: number;
  bottom: number;
}

/** The camera's padding, in screen px, when it frames a set of nodes or the whole graph. */
export const FIT_PADDING = 40;

export function nodeGeometry(theme: GeometryTokens) {
  const px = Number.parseFloat;
  const border = px(theme["border-width"]);
  const rowWidth = px(theme["node-width"]);
  const rowHeight = px(theme["node-height"]);
  // A frame's own border adds half its width to each side of its content, so the padding styles
  // are the sketch's outer padding less that half.
  const half = border / 2;
  const module: Padding = {
    top: px(theme["frame-pad-top"]) - half,
    left: px(theme["frame-pad-left"]) - half,
    right: px(theme["frame-pad-right"]) - half,
    bottom: px(theme["frame-pad-bottom"]) - half,
  };
  const klass: Padding = {
    top: px(theme["class-pad-top"]) - half,
    left: px(theme["class-pad-x"]) - half,
    right: px(theme["class-pad-x"]) - half,
    bottom: px(theme["class-pad-bottom"]) - half,
  };
  return {
    font: px(theme["node-name-size"]),
    border,
    /** Clearance kept around a new node. */
    margin: px(theme["spacing-md"]),
    /** Space between rows. */
    gap: px(theme["node-gap"]),
    /** A row's size as the stylesheet gives it, which Cytoscape draws a border wider. */
    width: rowWidth - border,
    height: rowHeight - border,
    /** A row's outer size, border included: what the preset reserves for it. */
    rowWidth,
    rowHeight,
    /** The height of a module or class with nothing inside it: one header row in a box. */
    headerHeight: px(theme["frame-header-height"]),
    /** The padding styles of a module and of a class frame. */
    module,
    class: klass,
    /** The sketch's outer paddings (border included), which the preset reserves. */
    outer: {
      module: {
        top: px(theme["frame-pad-top"]),
        left: px(theme["frame-pad-left"]),
        right: px(theme["frame-pad-right"]),
        bottom: px(theme["frame-pad-bottom"]),
      } satisfies Padding,
      class: {
        top: px(theme["class-pad-top"]),
        left: px(theme["class-pad-x"]),
        right: px(theme["class-pad-x"]),
        bottom: px(theme["class-pad-bottom"]),
      } satisfies Padding,
    },
    /** Room between modules the preset leaves for the edges that run between them. */
    gutter: px(theme["frame-gutter"]),
    rowGap: px(theme["frame-row-gap"]),
  };
}

/** A row's width inside `depth` class frames: each takes its side padding off both sides. */
export function rowWidthAt(theme: GeometryTokens, depth: number): number {
  const g = nodeGeometry(theme);
  return g.rowWidth - depth * (g.outer.class.left + g.outer.class.right);
}
