import { describe, expect, it } from "vitest";

import { colors, stateColors } from "../theme";
import { createEditorTheme } from "./theme";

describe("Monaco theme", () => {
  it("derives all colors, including low-chroma syntax and diff signs, from received tokens", () => {
    const palette = { ...colors, ...stateColors };
    const theme = createEditorTheme(palette);
    expect(theme.inherit).toBe(false);
    expect(theme.colors["editor.background"]).toBe(palette.background);
    expect(theme.colors["editor.foreground"]).toBe(palette.text);
    expect(theme.colors["diffEditor.insertedLineBackground"]).toBe(`${palette.added}10`);
    expect(theme.colors["diffEditor.removedLineBackground"]).toBe(`${palette.removed}10`);
    for (const color of Object.values(theme.colors)) {
      expect(Object.values(palette).some((token) => color.startsWith(token))).toBe(true);
    }
    for (const rule of theme.rules) {
      expect([palette.text, palette.muted]).toContain(rule.foreground);
    }
    const changed = createEditorTheme({
      ...palette,
      background: palette.panel,
      text: palette.muted,
    });
    expect(changed.colors["editor.background"]).toBe(palette.panel);
    expect(changed.rules[0]?.foreground).toBe(palette.muted);
  });
});
