import type { Box, LampGrid } from "./model";

/** Measure the painted overlay children, never their full-stage positioning wrapper. */
export function overlayBoxes(canvas: HTMLCanvasElement): Box[] {
  const stage =
    typeof canvas.closest === "function" ? canvas.closest('[aria-label="Graph stage"]') : null;
  if (!stage) return [];
  const origin = canvas.getBoundingClientRect();
  return [
    ...new Set([
      ...stage.querySelectorAll<HTMLElement>(".pointer-events-auto, [data-field-mask]"),
      ...canvas.ownerDocument.querySelectorAll<HTMLElement>(
        'dialog[open], [role="dialog"], .shell-sidebar, .agents-expanded, .narrow-notice, [data-field-mask]',
      ),
    ]),
  ]
    .filter((element) => !element.parentElement?.closest(".pointer-events-auto, [data-field-mask]"))
    .flatMap((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0
        ? [
            {
              x1: rect.left - origin.left,
              y1: rect.top - origin.top,
              x2: rect.right - origin.left,
              y2: rect.bottom - origin.top,
            },
          ]
        : [];
    });
}

export const contains = (boxes: readonly Box[], x: number, y: number) =>
  boxes.some((box) => x >= box.x1 && x < box.x2 && y >= box.y1 && y < box.y2);

/** Complement of the rectangle union: overlapping masks must never punch a hole in each other. */
export function unmaskedPath(boxes: readonly Box[], width: number, height: number): string {
  const xs = [...new Set([0, width, ...boxes.flatMap((box) => [box.x1, box.x2])])]
    .filter((x) => x >= 0 && x <= width)
    .sort((a, b) => a - b);
  const paths: string[] = [];
  for (let i = 1; i < xs.length; i++) {
    const left = xs[i - 1] ?? 0;
    const right = xs[i] ?? width;
    const intervals = boxes
      .filter((box) => box.x1 < right && box.x2 > left)
      .map((box) => [Math.max(0, box.y1), Math.min(height, box.y2)] as const)
      .filter(([top, bottom]) => bottom > top)
      .sort((a, b) => a[0] - b[0]);
    let top = 0;
    const add = (bottom: number) => {
      if (bottom > top)
        paths.push(
          `M${String(left)},${String(top)}H${String(right)}V${String(bottom)}H${String(left)}Z`,
        );
    };
    for (const [start, end] of intervals) {
      add(start);
      top = Math.max(top, end);
    }
    add(height);
  }
  return paths.join("");
}

/** Suppress whole LEDs at mask boundaries: a mask never leaves a chopped square or a guide dot. */
export function maskLamps(grid: LampGrid, cell: number, boxes: readonly Box[]) {
  for (const box of boxes) {
    const c0 = Math.max(0, Math.floor(box.x1 / cell));
    const c1 = Math.min(grid.cols, Math.ceil(box.x2 / cell));
    const r0 = Math.max(0, Math.floor(box.y1 / cell));
    const r1 = Math.min(grid.rows, Math.ceil(box.y2 / cell));
    for (let row = r0; row < r1; row++)
      for (let col = c0; col < c1; col++) {
        // Alpha 1/255 is reserved: lit levels start at 85/255; off remains zero for guide dots.
        grid.data[(row * grid.cols + col) * 4 + 3] = 1;
      }
  }
}
