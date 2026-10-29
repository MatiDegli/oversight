const widths = new Map<string, Map<string, number>>();
let canvas: HTMLCanvasElement | undefined;

/** Measure only loaded faces; never cache fallback-font metrics under an Atkinson font key. */
export function textWidth(value: string, font: string, size: number): number {
  if (typeof document !== "undefined" && "fonts" in document && document.fonts.check(font)) {
    const cached = widths.get(font)?.get(value);
    if (cached !== undefined) return cached;
    canvas ??= document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (context) {
      context.font = font;
      const width = context.measureText(value).width;
      const strings = widths.get(font) ?? new Map<string, number>();
      strings.set(value, width);
      widths.set(font, strings);
      return width;
    }
  }
  // jsdom/headless tests have no canvas or FontFaceSet. Keep the old estimate as a fallback.
  return value.length * size * 0.55;
}
