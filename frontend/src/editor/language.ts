export type EditorLanguage = "python" | "yaml" | "markdown" | "shell" | "ini" | "plaintext";

const extensions = {
  py: "python",
  yaml: "yaml",
  yml: "yaml",
  md: "markdown",
  markdown: "markdown",
  sh: "shell",
  bash: "shell",
  ini: "ini",
  toml: "ini",
  cfg: "ini",
  properties: "ini",
} satisfies Readonly<Record<string, EditorLanguage>>;

/** Highlighting only: TOML uses INI tokens; JSON requires its language service. */
export function languageForPath(path: string): EditorLanguage {
  const name = path.split("/").at(-1)?.toLowerCase() ?? "";
  if (name === ".gitconfig" || name === ".editorconfig") return "ini";
  const extension = name.includes(".") ? name.split(".").at(-1) : undefined;
  return extension && Object.hasOwn(extensions, extension)
    ? extensions[extension as keyof typeof extensions]
    : "plaintext";
}
