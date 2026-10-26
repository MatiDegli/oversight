import type { editor } from "monaco-editor";

export interface EditorColors {
  background: string;
  panel: string;
  text: string;
  muted: string;
  border: string;
  added: string;
  removed: string;
}

/** The only translation from received theme tokens into Monaco colors. */
export function createEditorTheme(colors: EditorColors): editor.IStandaloneThemeData {
  const { background, panel, text, muted, border, added, removed } = colors;
  return {
    base: "vs-dark",
    inherit: false,
    rules: [
      { token: "", foreground: text },
      { token: "comment", foreground: muted },
      { token: "keyword", foreground: muted },
      { token: "string", foreground: text },
      { token: "number", foreground: text },
      { token: "identifier", foreground: text },
      { token: "delimiter", foreground: muted },
    ],
    colors: {
      "editor.background": background,
      "editor.foreground": text,
      "editorLineNumber.foreground": muted,
      "editorLineNumber.activeForeground": text,
      "editorGutter.background": background,
      "editorWidget.background": panel,
      "editorWidget.border": border,
      "editor.selectionBackground": `${border}80`,
      "editor.inactiveSelectionBackground": `${border}40`,
      "editor.lineHighlightBorder": border,
      "editorCursor.foreground": text,
      focusBorder: text,
      "scrollbarSlider.background": `${muted}40`,
      "scrollbarSlider.hoverBackground": `${muted}80`,
      "scrollbarSlider.activeBackground": muted,
      "diffEditor.insertedTextBackground": `${added}20`,
      "diffEditor.removedTextBackground": `${removed}20`,
      "diffEditor.insertedLineBackground": `${added}10`,
      "diffEditor.removedLineBackground": `${removed}10`,
      "diffEditor.border": border,
      "diffEditorGutter.insertedLineBackground": `${added}20`,
      "diffEditorGutter.removedLineBackground": `${removed}20`,
    },
  };
}
