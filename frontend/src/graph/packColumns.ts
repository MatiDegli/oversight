import type { Point } from "@contracts";

export interface Card {
  id: string;
  width: number;
  height: number;
}

/** Stable column packing. Oversized cards get their own column; all other columns are bounded. */
export function packColumns(
  cards: readonly Card[],
  maxHeight: number,
  gutter: number,
  rowGap: number,
): Map<string, Point> {
  const positions = new Map<string, Point>();
  let x = 0;
  let y = 0;
  let width = 0;
  for (const card of cards) {
    if (y > 0 && y + card.height > maxHeight) {
      x += width + gutter;
      y = 0;
      width = 0;
    }
    positions.set(card.id, { x: x + card.width / 2, y: y + card.height / 2 });
    y += card.height + rowGap;
    width = Math.max(width, card.width);
  }
  return positions;
}
