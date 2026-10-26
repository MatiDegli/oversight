/** Monaco measures text at creation, so both families must finish loading first. */
export async function loadEditorFonts(fonts: Pick<FontFaceSet, "load" | "ready">): Promise<void> {
  await Promise.all([
    fonts.load('15px "Atkinson Hyperlegible Mono"'),
    fonts.load('15px "Atkinson Hyperlegible Next"'),
    fonts.ready,
  ]);
}
