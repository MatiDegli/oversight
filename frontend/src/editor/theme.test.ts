import { describe, expect, it } from "vitest";

import { themes } from "../theme";
import { createEditorTheme, editorTokens, tint } from "./theme";

describe("Monaco theme", () => {
  it("maps syntax, surfaces and diff tints to the active theme tokens", () => {
    const palette = themes.dark;
    const theme = createEditorTheme(palette);
    expect(theme.inherit).toBe(false);
    expect(theme.colors["editor.background"]).toBe(palette.page);
    expect(theme.colors["editor.foreground"]).toBe(palette["syntax-plain"]);
    expect(theme.colors["diffEditor.insertedLineBackground"]).toBe(tint(palette["added-tint"]));
    expect(theme.colors["diffEditor.removedLineBackground"]).toBe(tint(palette["removed-tint"]));
    expect(theme.colors["inputValidation.errorBackground"]).toBe(palette.field);
    expect(theme.colors["inputValidation.errorBorder"]).toBe(palette.text);
    // Only the tokens Monaco receives, so an agent color can never reach the editor.
    const allowed = editorTokens.flatMap((name) => {
      const value = palette[name];
      return typeof value === "object" ? [tint(value)] : [value];
    });
    for (const color of Object.values(theme.colors)) expect(allowed).toContain(color);
    for (const rule of theme.rules) expect(allowed).toContain(rule.foreground);
    expect(theme.rules).toEqual(
      expect.arrayContaining([
        { token: "comment", foreground: palette["code-dim"] },
        { token: "keyword", foreground: palette["syntax-keyword"] },
        { token: "string", foreground: palette["syntax-string"] },
        { token: "number", foreground: palette["syntax-number"] },
        { token: "predefined", foreground: palette["syntax-builtin"] },
        { token: "type.identifier", foreground: palette["syntax-definition"] },
      ]),
    );
    const changed = createEditorTheme({
      ...palette,
      page: palette.raised,
      "syntax-plain": palette.muted,
    });
    expect(changed.colors["editor.background"]).toBe(palette.raised);
    expect(changed.rules[0]?.foreground).toBe(palette.muted);
  });
});
