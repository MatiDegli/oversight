export type EditorLanguage = "python" | "yaml" | "markdown" | "shell" | "ini" | "plaintext";

const extensions: Readonly<Record<string, EditorLanguage>> = {
  py: "python",
  yaml: "yaml",
  yml: "yaml",
  md: "markdown",
  markdown: "markdown",
  sh: "shell",
  bash: "shell",
  ini: "ini",
  cfg: "ini",
  properties: "ini",
};

/** Highlighting only: Monaco ships no TOML definition; JSON requires its language service. */
export function languageForPath(path: string): EditorLanguage {
  const name = path.split("/").at(-1)?.toLowerCase() ?? "";
  if (name === ".gitconfig" || name === ".editorconfig") return "ini";
  const extension = name.includes(".") ? name.split(".").at(-1) : undefined;
  return extension && Object.hasOwn(extensions, extension)
    ? (extensions[extension] ?? "plaintext")
    : "plaintext";
}
