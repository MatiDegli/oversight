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
 * The letter badges of a node, right-aligned at `right` on the row centred at `centerY`. A badge is
 * a 20px square: filled in the agent's color with the letter in on-agent when the agent changes the
 * node, outlined in the agent's color when it only read it.
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
}

const wordText = (theme: LayerTheme, marks: NodeMarks, attributes: string) =>
  text(
    theme,
    attributes,
    theme["caption-size"],
    theme["label-weight"],
    slotColors(theme, marks.slot).text,
    marks.word,
  );

/**
 * The bottom row of a marked leaf: the state word at the left in its agent's text color, and the
 * letter badges at the right. The name stays above, in the node's own label.
 */
export function marksLayer(theme: LayerTheme, marks: NodeMarks, leaf: LeafSize): Layer {
  const size = px(theme["letter-badge-size"]);
  const pad = px(theme["spacing-md"]);
  const centerY = leaf.height - pad - size / 2;
  const word = wordText(theme, marks, `x="${String(pad)}" y="${String(centerY)}" dy=".35em"`);
  const badges = badgeShapes(theme, marks, leaf.width - pad, centerY);
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
const HEADER = { titleWidth: 240, countWidth: 180, height: 24, wordWidth: 64 };

const escape = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** One header-row image: a `width` by header-height SVG that clips what overruns it. */
function rowImage(theme: LayerTheme, width: number, body: string): string {
  return encode(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${String(width)}" height="${String(HEADER.height)}">${fontFaces(theme)}${body}</svg>`,
  );
}

/**
 * The title cut to the room it has, with an ellipsis, so a narrow frame never shows half a letter.
 * An SVG image cannot be measured here, so the width is an estimate of the type's average glyph;
 * it errs on the short side, and the image's own clip stays as the backstop.
 */
const GLYPH_EM = 0.55;
function fitTitle(theme: LayerTheme, title: string, room: number): string {
  const fit = Math.floor(room / (px(theme["label-size"]) * GLYPH_EM));
  if (title.length <= fit) return title;
  return fit > 1 ? `${title.slice(0, fit - 1)}\u2026` : "";
}

const rowBaseline = `y="${String(HEADER.height / 2)}" dy=".35em"`;

function titleLayer(
  theme: LayerTheme,
  title: string,
  width: number,
  offsetX = px(theme["spacing-lg"]),
): Layer {
  return {
    image: rowImage(
      theme,
      width,
      text(
        theme,
        `x="0" ${rowBaseline}`,
        theme["label-size"],
        theme["label-weight"],
        theme["module-label"],
        escape(title),
      ),
    ),
    width,
    height: HEADER.height,
    x: "0%",
    y: "0%",
    offsetX,
    offsetY: px(theme["spacing-md"]),
    clip: "node",
  };
}

/**
 * A frame's header row, inside its top padding: the title at the left in the module-label color and
 * the count at the right in the faint one. Two images, so each hugs its own edge whatever the
 * frame's width; each is wider than its text and clipped by the frame.
 *
 * A frame an agent touches says so in the count's place: the state word in the agent's text color
 * and its letter badges, right-aligned, with the count left out. The title then gets only the room
 * the frame's `width` leaves it, so the two never overlap.
 */
export function headerLayers(
  theme: LayerTheme,
  title: string,
  count: string,
  width: number,
  marks?: NodeMarks,
): Layer[] {
  const pad = px(theme["spacing-lg"]);
  const top = px(theme["spacing-md"]);
  if (marks) {
    const size = px(theme["letter-badge-size"]);
    const gap = px(theme["spacing-xs"]);
    const badges = marks.badges.length * size + (marks.badges.length - 1) * gap;
    const cluster = HEADER.wordWidth + gap + badges;
    const room = Math.max(0, Math.min(HEADER.titleWidth, width - 2 * pad - cluster - gap));
    const centerY = HEADER.height / 2;
    return [
      titleLayer(theme, fitTitle(theme, title, room), room),
      {
        image: rowImage(
          theme,
          cluster,
          wordText(
            theme,
            marks,
            `x="${String(cluster - badges - gap)}" y="${String(centerY)}" dy=".35em" text-anchor="end"`,
          ) + badgeShapes(theme, marks, cluster, centerY),
        ),
        width: cluster,
        height: HEADER.height,
        x: "100%",
        y: "0%",
        offsetX: -pad,
        offsetY: top,
        clip: "node",
      },
    ];
  }
  return [
    titleLayer(theme, title, HEADER.titleWidth),
    {
      image: rowImage(
        theme,
        HEADER.countWidth,
        text(
          theme,
          `x="${String(HEADER.countWidth)}" ${rowBaseline} text-anchor="end"`,
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

/**
 * A module or class with nothing inside it is drawn as a leaf-sized box, so its header stacks: the
 * title on the first row, and on the second its count, or, when an agent touches it, the state word
 * and badges in the count's place. The title gets the box's width less its padding.
 */
export function stackedHeaderLayers(
  theme: LayerTheme,
  title: string,
  count: string,
  leaf: LeafSize,
  marks?: NodeMarks,
): Layer[] {
  const pad = px(theme["spacing-md"]);
  const second: Layer = marks
    ? marksLayer(theme, marks, leaf)
    : {
        image: rowImage(
          theme,
          leaf.width,
          text(
            theme,
            `x="${String(pad)}" ${rowBaseline}`,
            theme["caption-size"],
            theme["caption-weight"],
            theme.faint,
            escape(count),
          ),
        ),
        width: leaf.width,
        height: HEADER.height,
        x: "0%",
        y: "0%",
        offsetX: 0,
        // Centred on the row a leaf's state word takes.
        offsetY: leaf.height - pad - px(theme["letter-badge-size"]) / 2 - HEADER.height / 2,
        clip: "node",
      };
  // The title starts at the second row's padding, which is a leaf's, narrower than a frame's.
  return [titleLayer(theme, title, leaf.width - 2 * pad, pad), second];
}
