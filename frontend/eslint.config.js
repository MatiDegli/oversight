// Lint rules, and the frontend's module boundaries (docs/proposal.md, section 3).
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const FEATURES = ["app", "state", "transport", "graph", "editor", "agents", "review", "theme"];
const OWNED_PACKAGES = {
  graph: ["cytoscape", "cytoscape-fcose"],
  editor: ["monaco-editor", "@monaco-editor/react"],
};

// Flat config replaces a rule's options per file instead of merging them, so each folder gets its
// whole list here.
function boundaries(feature) {
  const patterns = [
    { group: ["../*/*"], message: "Import another feature folder through its index.ts." },
  ];
  for (const [owner, packages] of Object.entries(OWNED_PACKAGES)) {
    if (owner !== feature) {
      patterns.push({
        group: packages,
        message: `Only src/${owner}/ imports ${packages.join(", ")}.`,
      });
    }
  }
  if (feature === "state") {
    patterns.push({ group: ["react", "react-dom"], message: "The reducer and store stay pure." });
  }
  const globalsRule =
    feature === "transport"
      ? ["off"]
      : [
          "error",
          { name: "fetch", message: "Only src/transport/ talks to the server." },
          { name: "EventSource", message: "Only src/transport/ talks to the server." },
        ];
  return {
    files: [`src/${feature}/**/*.{ts,tsx}`],
    rules: {
      "no-restricted-imports": ["error", { patterns }],
      "no-restricted-globals": globalsRule,
    },
  };
}

export default tseslint.config(
  { ignores: ["dist", "node_modules", "playwright-report", "test-results", "eslint.config.js"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/switch-exhaustiveness-check": "error",
    },
  },
  ...FEATURES.map(boundaries),
);
