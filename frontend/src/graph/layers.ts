import { agentLook } from "../theme";
import type { AgentFamily } from "../theme";
// What a node draws besides its box and its name: on a row, the state word and the letter badges,
// at the right of the name's line; on a module or class frame, its title and its count. Cytoscape
// draws a label in one color and font, so these are small SVG images on the node (as the distance
// badge is), each with its own font embedded, since an image cannot see the page's @font-face.
// Every color and size comes from the theme. Nothing here moves a node: the images sit inside boxes
// the preset already reserved.
import type { Slot } from "@contracts";
import regular from "@fontsource/atkinson-hyperlegible-next/files/atkinson-hyperlegible-next-latin-400-normal.woff2?inline";
import semibold from "@fontsource/atkinson-hyperlegible-next/files/atkinson-hyperlegible-next-latin-600-normal.woff2?inline";
import bold from "@fontsource/atkinson-hyperlegible-next/files/atkinson-hyperlegible-next-latin-700-normal.woff2?inline";

import { textWidth } from "./textWidth";
import type { MeasuredWord } from "./elements";

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
export interface LayerTheme extends Record<AgentFamily | `${AgentFamily}-text`, string> {
  "text-font": string;
  "on-agent": string;
  "module-label": string;
  faint: string;
  text: string;
  muted: string;
  "label-weight": number;
  "badge-weight": number;
  "agent-border-width": string;
  "border-width": string;
  "radius-node": string;
  "letter-badge-size": string;
  "letter-badge-font-size": string;
  "node-word-size": string;
  "node-word-gap": string;
  "node-pad-left": string;
  "node-pad-right": string;
  "frame-title-size": string;
  "frame-title-weight": number;
  "frame-count-size": string;
  "caption-weight": number;
  "frame-title-inset": string;
  "frame-header-center": string;
  "class-pad-top": string;
  "spacing-xs": string;
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
// Encoding the pieces preserves the exact URI bytes while encoding embedded fonts only once.
const encodedFonts = new Map<string, string>();
function image(theme: LayerTheme, open: string, body: string): string {
  const family = theme["text-font"].split(",")[0] ?? "sans-serif";
  let fonts = encodedFonts.get(family);
  if (fonts === undefined) {
    fonts = encodeURIComponent(fontFaces(theme));
    encodedFonts.set(family, fonts);
  }
  return `data:image/svg+xml,${encodeURIComponent(open)}${fonts}${encodeURIComponent(body + "</svg>")}`;
}

function fontFaces(theme: LayerTheme): string {
  const family = theme["text-font"].split(",")[0] ?? "sans-serif";
  const face = (weight: number, src: string) =>
    `@font-face{font-family:${family};src:url("${src}") format("woff2");font-weight:${String(weight)}}`;
  return `<style>${face(400, regular)}${face(600, semibold)}${face(700, bold)}</style>`;
}

const slotColors = (theme: LayerTheme, slot: Slot) => ({
  color: theme[agentLook[slot].family],
  text: theme[agentLook[slot].text],
});

const text = (
  theme: LayerTheme,
  attributes: string,
  size: string,
  weight: number,
  fill: string,
  body: string,
) =>
  `<text ${attributes} font-family='${theme["text-font"]}' font-size="${size}" font-weight="${String(weight)}" fill="${fill}">${body}</text>`;

/** The size of a row, which the image covers: the same box the stylesheet gives it. */
export interface LeafSize {
  width: number;
  height: number;
}

/** The width the badges of one node take together, side by side. */
const badgesWidth = (theme: LayerTheme, count: number) =>
  count * px(theme["letter-badge-size"]) + Math.max(0, count - 1) * px(theme["spacing-xs"]);

/**
 * The letter badges of a node, right-aligned at `right` on the row centred at `centerY`. A badge is
 * a square, 26px at the sketch's scale: filled in the agent's color with the letter in on-agent when
 * the agent changes the node, outlined in the agent's color when it only read it.
 */
function badgeShapes(theme: LayerTheme, marks: NodeMarks, right: number, centerY: number): string {
  const size = px(theme["letter-badge-size"]);
  const gap = px(theme["spacing-xs"]);
  const stroke = px(theme["agent-border-width"]);
  return marks.badges
    .map(({ slot, filled }, index) => {
      const { color, text: ink } = slotColors(theme, slot);
      // The last badge sits at the right edge; earlier ones step left of it.
      const x =
        right - size * (marks.badges.length - index) - gap * (marks.badges.length - 1 - index);
      const square = filled
        ? `<rect x="${String(x)}" y="${String(centerY - size / 2)}" width="${String(size)}" height="${String(size)}" rx="${theme["radius-node"]}" fill="${color}"/>`
        : `<rect x="${String(x + stroke / 2)}" y="${String(centerY - size / 2 + stroke / 2)}" width="${String(size - stroke)}" height="${String(size - stroke)}" rx="${theme["radius-node"]}" fill="none" stroke="${color}" stroke-width="${String(stroke)}"/>`;
      const letter = text(
        theme,
        `x="${String(x + size / 2)}" y="${String(centerY)}" dy=".35em" text-anchor="middle"`,
        theme["letter-badge-font-size"],
        theme["badge-weight"],
        filled ? theme["on-agent"] : ink,
        slot,
      );
      return square + letter;
    })
    .join("");
}

/** The state word, right-aligned so that it ends `gap` left of `right`. */
const wordText = (theme: LayerTheme, marks: NodeMarks, attributes: string) =>
  text(
    theme,
    attributes,
    theme["node-word-size"],
    theme["label-weight"],
    slotColors(theme, marks.slot).text,
    marks.word,
  );

/**
 * What the state word and the badges take at the right of a row: the width the name must leave
 * free. The name's own room is the row's width less its two paddings and this.
 */
export function marksClearance(theme: LayerTheme, marks: NodeMarks): number {
  const word = textWidth(
    marks.word,
    `${String(theme["label-weight"])} ${theme["node-word-size"]} ${theme["text-font"]}`,
    px(theme["node-word-size"]),
  );
  return word + px(theme["node-word-gap"]) + badgesWidth(theme, marks.badges.length);
}

/**
 * A marked row's right end, on the line of its name: the state word in its agent's text color, then
 * the letter badges at the right padding. The name stays in the node's own label, at the left.
 */
export function marksLayer(theme: LayerTheme, marks: NodeMarks, leaf: LeafSize): Layer {
  // The image starts at the node's body, half a border inside its outer edge.
  const right = leaf.width - (px(theme["node-pad-right"]) - px(theme["border-width"]) / 2);
  const centerY = leaf.height / 2;
  const badges = badgesWidth(theme, marks.badges.length);
  const word = wordText(
    theme,
    marks,
    `x="${String(right - badges - px(theme["node-word-gap"]))}" y="${String(centerY)}" dy=".35em" text-anchor="end"`,
  );
  const shapes = badgeShapes(theme, marks, right, centerY);
  const open = `<svg xmlns="http://www.w3.org/2000/svg" width="${String(leaf.width)}" height="${String(leaf.height)}">`;
  return {
    image: image(theme, open, word + shapes),
    width: leaf.width,
    height: leaf.height,
    x: "0%",
    y: "0%",
    offsetX: 0,
    offsetY: 0,
    clip: "node",
  };
}

/** Review evidence occupies the state-word slot without changing the node's box or light. */
export function measuredLayer(
  theme: LayerTheme,
  word: Extract<MeasuredWord, "differs" | "varied">,
  leaf: LeafSize,
): Layer {
  const width = HEADER.wordWidth;
  return {
    image: rowImage(
      theme,
      width,
      text(
        theme,
        `x="${String(width)}" ${rowBaseline} text-anchor="end"`,
        theme["node-word-size"],
        word === "differs" ? 600 : 400,
        word === "differs" ? theme.text : theme.muted,
        word,
      ),
    ),
    width,
    height: HEADER.height,
    x: "100%",
    y: "0%",
    offsetX: -px(theme["node-pad-right"]) - px(theme["node-word-gap"]),
    offsetY: (leaf.height - HEADER.height) / 2,
    clip: "node",
  };
}

/** The room the title and the count each get on a frame's header row, in px. */
export const HEADER = { titleWidth: 240, countWidth: 180, height: 28, wordWidth: 70 };

const escape = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** One header-row image: a `width` by header-height SVG that clips what overruns it. */
function rowImage(theme: LayerTheme, width: number, body: string): string {
  return image(
    theme,
    `<svg xmlns="http://www.w3.org/2000/svg" width="${String(width)}" height="${String(HEADER.height)}">`,
    body,
  );
}

/**
 * The title cut to the room it has, with an ellipsis, so a narrow frame never shows half a letter.
 * The canvas uses the same loaded face as the SVG; the image's clip stays as the backstop.
 */
function fitTitle(theme: LayerTheme, title: string, room: number): string {
  const width = (value: string) =>
    textWidth(
      value,
      `${String(theme["frame-title-weight"])} ${theme["frame-title-size"]} ${theme["text-font"]}`,
      px(theme["frame-title-size"]),
    );
  if (width(title) <= room) return title;
  if (width("\u2026") > room) return "";
  let low = 0;
  let high = title.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (width(`${title.slice(0, middle)}\u2026`) <= room) low = middle;
    else high = middle - 1;
  }
  return `${title.slice(0, low)}\u2026`;
}

const rowBaseline = `y="${String(HEADER.height / 2)}" dy=".35em"`;

/** Where a frame's header row sits: its centre's distance from the frame's top. */
export type Band = "module" | "class" | "empty";

function bandCenter(theme: LayerTheme, band: Band, height: number): number {
  return band === "module"
    ? px(theme["frame-header-center"])
    : band === "class"
      ? px(theme["class-pad-top"]) / 2
      : height / 2;
}

function titleLayer(
  theme: LayerTheme,
  title: string,
  width: number,
  offsetY: number,
  folded = false,
): Layer {
  return {
    image: rowImage(
      theme,
      width,
      text(
        theme,
        `x="${folded ? "14" : "0"}" ${rowBaseline}`,
        theme["frame-title-size"],
        theme["frame-title-weight"],
        theme["module-label"],
        escape(fitTitle(theme, title, Math.max(0, width - (folded ? 14 : 0)))),
      ) +
        (folded
          ? `<path d="M1 9 L7 14 L1 19" fill="none" stroke="${theme["module-label"]}" stroke-width="${theme["border-width"]}"/>`
          : ""),
    ),
    width,
    height: HEADER.height,
    x: "0%",
    y: "0%",
    offsetX: px(theme["frame-title-inset"]) - px(theme["border-width"]) / 2,
    offsetY,
    clip: "node",
  };
}

/**
 * A frame's header row: the title at the left in the module-label color and the count at the right
 * in the faint one, on the line of `band`: a module's header band, a class's, or the middle of a
 * frame with nothing in it. Two images, so each hugs its own edge whatever the frame's width; each
 * is wider than its text and clipped by the frame.
 *
 * A frame an agent touches says so in the count's place: the state word in the agent's text color
 * and its letter badges, right-aligned, with the count left out. The title then gets only the room
 * the frame's `width` leaves it, so the two never overlap.
 */
export function headerLayers(
  theme: LayerTheme,
  title: string,
  count: string,
  size: LeafSize,
  band: Band,
  marks?: NodeMarks,
  folded = false,
  measured?: "differs" | "varied",
): Layer[] {
  const inset = px(theme["frame-title-inset"]);
  const top = bandCenter(theme, band, size.height) - HEADER.height / 2;
  const trailing = (width: number, body: string, offsetX: number): Layer => ({
    image: rowImage(theme, width, body),
    width,
    height: HEADER.height,
    x: "100%",
    y: "0%",
    offsetX,
    offsetY: top,
    clip: "node",
  });
  // Agent marks own this slot even when a hidden caller differs.
  if (marks) {
    const badges = badgesWidth(theme, marks.badges.length);
    const gap = px(theme["node-word-gap"]);
    const cluster = HEADER.wordWidth + gap + badges;
    const room = Math.max(0, Math.min(HEADER.titleWidth, size.width - 2 * inset - cluster - gap));
    const centerY = HEADER.height / 2;
    return [
      titleLayer(theme, title, room, top, folded),
      trailing(
        cluster,
        wordText(
          theme,
          marks,
          `x="${String(cluster - badges - gap)}" y="${String(centerY)}" dy=".35em" text-anchor="end"`,
        ) + badgeShapes(theme, marks, cluster, centerY),
        -inset,
      ),
    ];
  }
  const countWidth = textWidth(
    count,
    `${String(theme["caption-weight"])} ${theme["frame-count-size"]} ${theme["text-font"]}`,
    px(theme["frame-count-size"]),
  );
  if (measured) {
    const word = measuredLayer(theme, measured, { width: size.width, height: HEADER.height });
    const gap = px(theme["node-word-gap"]);
    const room = Math.max(
      0,
      Math.min(HEADER.titleWidth, size.width - 2 * inset - word.width - countWidth - 2 * gap),
    );
    return [
      titleLayer(theme, title, room, top, folded),
      countLayer(theme, count, top, word.offsetX - word.width - gap),
      { ...word, offsetY: top },
    ];
  }
  const room = Math.max(
    0,
    Math.min(
      HEADER.titleWidth,
      size.width - 2 * inset - Math.min(HEADER.countWidth, countWidth) - px(theme["node-word-gap"]),
    ),
  );
  return [titleLayer(theme, title, room, top, folded), countLayer(theme, count, top, -inset)];
}

/** The same neutral count in plain and measured frame headers. */
function countLayer(theme: LayerTheme, count: string, top: number, offsetX: number): Layer {
  return {
    image: rowImage(
      theme,
      HEADER.countWidth,
      text(
        theme,
        `x="${String(HEADER.countWidth)}" ${rowBaseline} text-anchor="end"`,
        theme["frame-count-size"],
        theme["caption-weight"],
        theme.faint,
        escape(count),
      ),
    ),
    width: HEADER.countWidth,
    height: HEADER.height,
    x: "100%",
    y: "0%",
    offsetX,
    offsetY: top,
    clip: "node",
  };
}
