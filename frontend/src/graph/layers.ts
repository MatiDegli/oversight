// What a node draws besides its box and its name: on a leaf, the state word and the letter badges;
// on a module or class frame, its title and its count. Cytoscape draws a label in one color and
// font, so these are small SVG images on the node (as the distance badge is), each with its own
// font embedded, since an image cannot see the page's @font-face. Every color and size comes from
// the theme. Nothing here moves a node: the images sit inside boxes the preset already reserved.
import type { Slot } from "@contracts";
import regular from "@fontsource/atkinson-hyperlegible-next/files/atkinson-hyperlegible-next-latin-400-normal.woff2?inline";
import semibold from "@fontsource/atkinson-hyperlegible-next/files/atkinson-hyperlegible-next-latin-600-normal.woff2?inline";
import bold from "@fontsource/atkinson-hyperlegible-next/files/atkinson-hyperlegible-next-latin-700-normal.woff2?inline";

/** The words a node says about what an agent is doing with it, in order of precedence. */
export type Word = "editing" | "changed" | "new" | "read";

export interface NodeMarks {
  /** The strongest state on the node and the agent whose it is: the word is in that agent's color. */
  word: Word;
  slot: Slot;
  /** One letter badge per agent that touches the node, A first; filled unless the agent only read. */
  badges: { slot: Slot; filled: boolean }[];
}

/** The theme values the layers read; the full theme satisfies it. */
export interface LayerTheme {
  "text-font": string;
  "agent-a": string;
  "agent-a-text": string;
  "agent-b": string;
  "agent-b-text": string;
  "on-agent": string;
  "module-label": string;
  faint: string;
  "label-size": string;
  "label-weight": number;
  "caption-size": string;
  "caption-weight": number;
  "badge-size": string;
  "badge-weight": number;
  "agent-border-width": string;
  "radius-label": string;
  "letter-badge-size": string;
  "spacing-xs": string;
  "spacing-md": string;
  "spacing-lg": string;
}

/** One image on a node, in the units of Cytoscape's background-* properties. */
export interface Layer {
  image: string;
  width: number;
  height: number;
  x: string;
  y: string;
  offsetX: number;
  offsetY: number;
  clip: "node" | "none";
}

const px = Number.parseFloat;
const encode = (svg: string) => `data:image/svg+xml,${encodeURIComponent(svg)}`;

function fontFaces(theme: LayerTheme): string {
  const family = theme["text-font"].split(",")[0] ?? "sans-serif";
  const face = (weight: number, src: string) =>
    `@font-face{font-family:${family};src:url("${src}") format("woff2");font-weight:${String(weight)}}`;
  return `<style>${face(400, regular)}${face(600, semibold)}${face(700, bold)}</style>`;
}

const slotColors = (theme: LayerTheme, slot: Slot) =>
  slot === "A"
    ? { color: theme["agent-a"], text: theme["agent-a-text"] }
    : { color: theme["agent-b"], text: theme["agent-b-text"] };

const text = (
  theme: LayerTheme,
  attributes: string,
  size: string,
  weight: number,
  fill: string,
  body: string,
) =>
  `<text ${attributes} font-family='${theme["text-font"]}' font-size="${size}" font-weight="${String(weight)}" fill="${fill}">${body}</text>`;

/** The size of a leaf node, which the image covers: the same box the stylesheet gives it. */
export interface LeafSize {
  width: number;
  height: number;
}

/**
 * The bottom row of a marked leaf: the state word at the left in its agent's text color, and the
 * letter badges at the right. A badge is a 20px square: filled in the agent's color with the letter
 * in on-agent when the agent changes the node, outlined in the agent's color when it only read it.
 * The name stays above, in the node's own label.
 */
export function marksLayer(theme: LayerTheme, marks: NodeMarks, leaf: LeafSize): Layer {
  const size = px(theme["letter-badge-size"]);
  const pad = px(theme["spacing-md"]);
  const gap = px(theme["spacing-xs"]);
  const centerY = leaf.height - pad - size / 2;
  const stroke = px(theme["agent-border-width"]);
  const badges = marks.badges
    .map(({ slot, filled }, index) => {
      const { color, text: ink } = slotColors(theme, slot);
      // The last badge sits at the right padding; earlier ones step left of it.
      const x =
        leaf.width -
        pad -
        size * (marks.badges.length - index) -
        gap * (marks.badges.length - 1 - index);
      const square = filled
        ? `<rect x="${String(x)}" y="${String(centerY - size / 2)}" width="${String(size)}" height="${String(size)}" rx="${theme["radius-label"]}" fill="${color}"/>`
        : `<rect x="${String(x + stroke / 2)}" y="${String(centerY - size / 2 + stroke / 2)}" width="${String(size - stroke)}" height="${String(size - stroke)}" rx="${theme["radius-label"]}" fill="none" stroke="${color}" stroke-width="${String(stroke)}"/>`;
      const letter = text(
        theme,
        `x="${String(x + size / 2)}" y="${String(centerY)}" dy=".35em" text-anchor="middle"`,
        theme["badge-size"],
        theme["badge-weight"],
        filled ? theme["on-agent"] : ink,
        slot,
      );
      return square + letter;
    })
    .join("");
  const word = text(
    theme,
    `x="${String(pad)}" y="${String(centerY)}" dy=".35em"`,
    theme["caption-size"],
    theme["label-weight"],
    slotColors(theme, marks.slot).text,
    marks.word,
  );
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${String(leaf.width)}" height="${String(leaf.height)}">${fontFaces(theme)}${word}${badges}</svg>`;
  return {
    image: encode(svg),
    width: leaf.width,
    height: leaf.height,
    x: "0%",
    y: "0%",
    offsetX: 0,
    offsetY: 0,
    clip: "node",
  };
}

/** The room the title and the count each get on a frame's header row, in px. */
const HEADER = { titleWidth: 240, countWidth: 180, height: 24 };

/**
 * A frame's header row, inside its top padding: the title at the left in the module-label color and
 * the count at the right in the faint one. Two images, so each hugs its own edge whatever the
 * frame's width; each is wider than its text and clipped by the frame.
 */
export function headerLayers(theme: LayerTheme, title: string, count: string): Layer[] {
  const pad = px(theme["spacing-lg"]);
  const top = px(theme["spacing-md"]);
  const baseline = `y="${String(HEADER.height / 2)}" dy=".35em"`;
  const image = (width: number, body: string) =>
    encode(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${String(width)}" height="${String(HEADER.height)}">${fontFaces(theme)}${body}</svg>`,
    );
  const escape = (value: string) =>
    value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return [
    {
      image: image(
        HEADER.titleWidth,
        text(
          theme,
          `x="0" ${baseline}`,
          theme["label-size"],
          theme["label-weight"],
          theme["module-label"],
          escape(title),
        ),
      ),
      width: HEADER.titleWidth,
      height: HEADER.height,
      x: "0%",
      y: "0%",
      offsetX: pad,
      offsetY: top,
      clip: "node",
    },
    {
      image: image(
        HEADER.countWidth,
        text(
          theme,
          `x="${String(HEADER.countWidth)}" ${baseline} text-anchor="end"`,
          theme["caption-size"],
          theme["caption-weight"],
          theme.faint,
          escape(count),
        ),
      ),
      width: HEADER.countWidth,
      height: HEADER.height,
      x: "100%",
      y: "0%",
      offsetX: -pad,
      offsetY: top,
      clip: "node",
    },
  ];
}
