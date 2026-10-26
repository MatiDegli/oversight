import type { editor } from "monaco-editor";

const solidTokens = [
  "page",
  "field",
  "raised",
  "control-edge",
  "text",
  "muted",
  "faint",
  "page-border",
  "border",
  "added",
  "removed",
  "code-dim",
  "syntax-plain",
  "syntax-keyword",
  "syntax-string",
  "syntax-number",
  "syntax-builtin",
  "syntax-definition",
] as const;
const tintTokens = ["added-tint", "removed-tint", "span-highlight", "fold-tint"] as const;
/** The tokens Monaco receives: every color the editor paints is one of these. */
export const editorTokens = [...solidTokens, ...tintTokens];
export type EditorColors = Record<(typeof solidTokens)[number], string> &
  Record<(typeof tintTokens)[number], { color: string; alpha: number }>;

/** Monaco requires hex alpha; the color and opacity both come from the token. */
export function tint(token: { color: string; alpha: number }): string {
  return `${token.color}${Math.round(token.alpha * 255)
    .toString(16)
    .padStart(2, "0")}`;
}

/** The only translation from received theme tokens into Monaco colors. */
export function createEditorTheme(colors: EditorColors): editor.IStandaloneThemeData {
  const { page, raised, text, muted, faint, border, added, removed } = colors;
  return {
    base: "vs-dark",
    inherit: false,
    rules: [
      { token: "", foreground: colors["syntax-plain"] },
      { token: "comment", foreground: colors["code-dim"] },
      { token: "keyword", foreground: colors["syntax-keyword"] },
      { token: "string", foreground: colors["syntax-string"] },
      { token: "number", foreground: colors["syntax-number"] },
      { token: "predefined", foreground: colors["syntax-builtin"] },
      { token: "type.identifier", foreground: colors["syntax-definition"] },
      { token: "identifier", foreground: colors["syntax-plain"] },
      { token: "delimiter", foreground: colors["syntax-plain"] },
    ],
    colors: {
      "editor.background": page,
      "editor.foreground": colors["syntax-plain"],
      "editorLineNumber.foreground": colors["code-dim"],
      "editorLineNumber.activeForeground": muted,
      "editorGutter.background": page,
      "editorWidget.background": raised,
      "editorWidget.border": border,
      "editor.selectionBackground": tint(colors["span-highlight"]),
      "editor.inactiveSelectionBackground": tint(colors["span-highlight"]),
      "editor.lineHighlightBackground": tint(colors["span-highlight"]),
      "editor.lineHighlightBorder": colors["page-border"],
      "editorCursor.foreground": text,
      "editorCursor.background": page,
      "editorWhitespace.foreground": faint,
      "editorIndentGuide.background1": colors["page-border"],
      "editorIndentGuide.activeBackground1": border,
      "editorBracketMatch.background": tint(colors["span-highlight"]),
      "editorBracketMatch.border": border,
      "editorOverviewRuler.border": colors["page-border"],
      "editorOverviewRuler.background": page,
      "editorGutter.foldingControlForeground": muted,
      "editor.foldBackground": tint(colors["fold-tint"]),
      focusBorder: text,
      "scrollbarSlider.background": border,
      "scrollbarSlider.hoverBackground": muted,
      "scrollbarSlider.activeBackground": faint,
      "diffEditor.insertedTextBackground": tint(colors["added-tint"]),
      "diffEditor.removedTextBackground": tint(colors["removed-tint"]),
      "diffEditor.insertedLineBackground": tint(colors["added-tint"]),
      "diffEditor.removedLineBackground": tint(colors["removed-tint"]),
      "diffEditor.border": colors["page-border"],
      "diffEditor.diagonalFill": border,
      "diffEditorGutter.insertedLineBackground": tint(colors["added-tint"]),
      "diffEditorGutter.removedLineBackground": tint(colors["removed-tint"]),
      "diffEditorOverview.insertedForeground": added,
      "diffEditorOverview.removedForeground": removed,
      "diffEditor.unchangedRegionBackground": raised,
      "diffEditor.unchangedRegionForeground": muted,
      "diffEditor.unchangedCodeBackground": tint(colors["fold-tint"]),
      // Monaco's defaults for these are blue, orange or amber; each reads as an agent or a warning.
      foreground: text,
      descriptionForeground: muted,
      disabledForeground: faint,
      errorForeground: text,
      "icon.foreground": muted,
      "textLink.foreground": text,
      "textLink.activeForeground": text,
      "editorWidget.foreground": text,
      "editorHoverWidget.foreground": text,
      "editorHoverWidget.highlightForeground": text,
      "editorHoverWidget.statusBarBackground": raised,
      "editor.hoverHighlightBackground": tint(colors["span-highlight"]),
      "editor.rangeHighlightBackground": tint(colors["span-highlight"]),
      "editor.foldPlaceholderForeground": muted,
      "editorOverviewRuler.bracketMatchForeground": muted,
      "editorUnicodeHighlight.border": text,
      // Find (Ctrl+F) marks matches with the neutral highlight and a border, never a hue.
      "editor.findMatchBackground": tint(colors["span-highlight"]),
      "editor.findMatchBorder": text,
      "editor.findMatchHighlightBackground": tint(colors["span-highlight"]),
      "editor.findMatchHighlightBorder": muted,
      "editor.findRangeHighlightBackground": tint(colors["fold-tint"]),
      "editorOverviewRuler.findMatchForeground": muted,
      "input.background": colors.field,
      "input.foreground": text,
      "input.border": colors["control-edge"],
      "input.placeholderForeground": faint,
      "inputOption.activeBorder": text,
      "inputOption.activeForeground": text,
      "inputOption.activeBackground": tint(colors["span-highlight"]),
      "inputOption.hoverBackground": tint(colors["span-highlight"]),
      "toolbar.hoverBackground": tint(colors["span-highlight"]),
      "toolbar.activeBackground": tint(colors["span-highlight"]),
    },
  };
}
