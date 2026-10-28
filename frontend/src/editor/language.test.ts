import { expect, it } from "vitest";

import { languageForPath } from "./language";

it.each([
  ["shop/cart.py", "python"],
  ["config.yaml", "yaml"],
  ["config.yml", "yaml"],
  ["README.md", "markdown"],
  ["notes.markdown", "markdown"],
  ["setup.sh", "shell"],
  ["setup.bash", "shell"],
  ["settings.ini", "ini"],
  ["settings.cfg", "ini"],
  ["settings.properties", "ini"],
  [".gitconfig", "ini"],
  [".editorconfig", "ini"],
  ["package.json", "plaintext"],
  ["pyproject.toml", "plaintext"],
  ["unknown.xyz", "plaintext"],
  ["unknown.constructor", "plaintext"],
  ["no-extension", "plaintext"],
  ["yaml/file", "plaintext"],
  ["folder.py/file.txt", "plaintext"],
  ["folder/CONFIG.YML", "yaml"],
])("maps %s to %s", (path, expected) => {
  expect(languageForPath(path)).toBe(expected);
});
