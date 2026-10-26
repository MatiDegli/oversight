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

const SERVER_GLOBALS = ["fetch", "EventSource"];

// The packages a folder must not import: each owner's, and for state/ the UI framework. Every entry
// feeds both the static rule and the dynamic one, so the two cannot drift apart.
function restrictedPackages(feature) {
  const restricted = [];
  for (const [owner, packages] of Object.entries(OWNED_PACKAGES)) {
    if (owner !== feature) {
      restricted.push({ packages, message: `Only src/${owner}/ imports ${packages.join(", ")}.` });
    }
  }
  if (feature === "state") {
    restricted.push({
      packages: ["react", "react-dom"],
      message: "The reducer and store stay pure.",
    });
  }
  return restricted;
}

// The source of a regex matching a package or any of its subpaths.
function packagesSource(packages) {
  const names = packages.map((name) => name.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&")).join("|");
  return `^(?:${names})(?:/.*)?$`;
}

// no-restricted-imports does not see import("..."), so a syntax selector covers it, for every
// restriction the static rule has. esquery's regex literal cannot hold a slash, hence \x2f. A
// template literal without expressions counts as well.
function dynamicImportSelectors({ source, message }) {
  const regex = source.replaceAll("/", "\\x2f");
  return [
    `ImportExpression[source.value=/${regex}/]`,
    `ImportExpression[source.quasis.0.value.cooked=/${regex}/]`,
  ].map((selector) => ({ selector, message }));
}

// Reaching into another feature: a relative path that climbs into a feature other than its own and
// names something there besides its index. The folder itself (../../state) and its index pass, at
// any depth. A feature folder therefore cannot hold a subfolder named like another feature.
function internalsPattern(feature, prefix) {
  const others = FEATURES.filter((other) => other !== feature).join("|");
  return {
    regex: `^${prefix}(?:${others})/(?!index(?:\\.\\w+)?$).+`,
    message: "Import another feature folder through its index.ts.",
  };
}

// Flat config replaces a rule's options per file instead of merging them, so each block gets its
// whole list here. A null feature is the files directly in src/, such as main.tsx.
function boundaries(feature) {
  const restricted = restrictedPackages(feature);
  const internals = internalsPattern(feature, feature === null ? "\\./" : "(?:\\.\\./)+");
  const patterns = [
    internals,
    ...restricted.map(({ packages, message }) => ({ group: packages, message })),
  ];
  const dynamicImports = [
    dynamicImportSelectors({ source: internals.regex, message: internals.message }),
    ...restricted.map(({ packages, message }) =>
      dynamicImportSelectors({ source: packagesSource(packages), message }),
    ),
  ].flat();
  const globalsRule =
    feature === "transport"
      ? ["off"]
      : [
          "error",
          {
            checkGlobalObject: true,
            globals: SERVER_GLOBALS.map((name) => ({
              name,
              message: "Only src/transport/ talks to the server.",
            })),
          },
        ];
  return {
    files: [feature === null ? "src/*.{ts,tsx}" : `src/${feature}/**/*.{ts,tsx}`],
    rules: {
      "no-restricted-imports": ["error", { patterns }],
      "no-restricted-syntax": ["error", ...dynamicImports],
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
  ...[null, ...FEATURES].map(boundaries),
);
