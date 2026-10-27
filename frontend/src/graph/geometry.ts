// Sizes the graph draws and the preset reserves, derived from theme tokens in one place so the
// stylesheet (styles.ts), the placement of new nodes (updateGraph.ts) and the preset generator
// (tools/layout.ts) cannot drift apart. This file imports nothing at run time: the generator runs
// it under plain Node.

/** The tokens the geometry reads; a full theme satisfies it. */
export interface GeometryTokens {
  "body-size": string;
  "spacing-md": string;
  "spacing-lg": string;
  "border-width": string;
}

/** The camera fits the graph with this padding, in screen px, on every side. */
export const FIT_PADDING = 40;

export function nodeGeometry(theme: GeometryTokens) {
  const px = Number.parseFloat;
  const font = px(theme["body-size"]);
  const border = px(theme["border-width"]);
  const margin = px(theme["spacing-md"]);
  /** A parent's padding is three margins; a frame adds its own border to that. */
  const padding = margin * 3;
  return {
    font,
    border,
    /** Clearance kept around a new node. */
    margin,
    /** Space between siblings. */
    gap: px(theme["spacing-lg"]),
    /** A leaf's width and height, in units of the graph's body type. */
    width: font * 10,
    height: font * 4,
    padding,
    /** What a parent's frame adds beyond its children on each side. */
    inset: padding + border,
    /** A parent's label sits above its frame, one line of body type tall. */
    label: font,
  };
}
