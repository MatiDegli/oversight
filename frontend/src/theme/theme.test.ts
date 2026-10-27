import { readFileSync } from "node:fs";

import { compile } from "tailwindcss";
import { describe, expect, it } from "vitest";

import { getActiveTheme, themes } from "./index";

const css = readFileSync(new URL("./index.css", import.meta.url), "utf8");
const ts = readFileSync(new URL("./index.ts", import.meta.url), "utf8");
const design = readFileSync(new URL("../../../DESIGN.md", import.meta.url), "utf8");
const tokens = themes.dark;
// Monaco consumes these through TypeScript, so none may appear in CSS.
const tsOnly = [
  "syntax-plain",
  "syntax-keyword",
  "syntax-string",
  "syntax-number",
  "syntax-builtin",
  "syntax-definition",
  "code-dim",
  "span-highlight",
  "fold-tint",
] as const;
const radiusBlock = /@theme\s*\{([^}]+)\}/;
const rootBlock = /:root\s*\{([^}]+)\}/;
const themeBlock = /@theme inline\s*\{([^}]+)\}/;
const darkBlock = /:root:not\(\[data-theme\]\),\s*:root\[data-theme="dark"\]\s*\{([^}]+)\}/;

function required(value: string | undefined): string {
  if (value === undefined) throw new Error("Missing token value");
  return value;
}

function declarations(block: string): Record<string, string> {
  const entries = [...block.matchAll(/--([\w*-]+)\s*:\s*([^;]+);/g)].map<[string, string]>(
    (match) => [required(match[1]), required(match[2]).trim()],
  );
  expect(new Set(entries.map(([name]) => name)).size, "No duplicate declarations").toBe(
    entries.length,
  );
  return Object.fromEntries(entries);
}

const colors = Object.entries(tokens).filter(
  ([name, value]) =>
    !tsOnly.some((token) => token === name) &&
    (typeof value === "object" || (typeof value === "string" && value.startsWith("#"))),
);
const radii = Object.fromEntries(
  Object.entries(tokens).filter(([name]) => name.startsWith("radius-")),
);
const { "radius-*": radiusReset, ...cssRadii } = declarations(radiusBlock.exec(css)?.[1] ?? "");
const dark = declarations(darkBlock.exec(css)?.[1] ?? "");
const global = declarations(rootBlock.exec(css)?.[1] ?? "");
const shared = { ...dark, ...global, ...cssRadii };

// Preserve alpha as a number instead of baking it into an eight-digit hex string.
function rgba(red: string, green: string, blue: string, alpha: string) {
  return {
    color: `#${[red, green, blue].map((part) => Number(part).toString(16).padStart(2, "0")).join("")}`,
    alpha: Number(alpha),
  };
}

function cssValue(value: string): unknown {
  const alias = /^var\(--([\w-]+)\)$/.exec(value);
  if (alias) return cssValue(required(shared[required(alias[1])]));
  const mix = /^color-mix\(in srgb, var\(--([\w-]+)\) ([\d.]+)%, transparent\)$/.exec(value);
  if (mix)
    return { color: cssValue(required(shared[required(mix[1])])), alpha: Number(mix[2]) / 100 };
  const channels = /^rgba\((\d+), (\d+), (\d+), ([\d.]+)\)$/.exec(value);
  if (channels)
    return rgba(
      required(channels[1]),
      required(channels[2]),
      required(channels[3]),
      required(channels[4]),
    );
  return /^\d+(?:\.\d+)?$/.test(value) ? Number(value) : value;
}

describe("theme", () => {
  it("keeps every shared CSS token equal to its TypeScript value, including tint alpha", () => {
    expect(Object.keys(shared).length).toBeGreaterThan(0);
    const expected = Object.fromEntries(
      Object.entries(tokens).filter(([name]) => !tsOnly.some((token) => token === name)),
    );
    // Whole-object equality rejects missing counterparts in either direction.
    expect(
      Object.fromEntries(Object.entries(shared).map(([key, value]) => [key, cssValue(value)])),
    ).toEqual(expected);
    expect(
      declarations(
        css
          .replace(darkBlock, "")
          .replace(rootBlock, "")
          .replace(radiusBlock, "")
          .replace(themeBlock, ""),
      ),
    ).toEqual({});
    for (const name of tsOnly) expect(css).not.toContain(`--${name}:`);
  });

  it("maps every shared color to a Tailwind utility and removes the default palette", () => {
    const block = themeBlock.exec(css)?.[1] ?? "";
    expect(declarations(block)).toEqual({
      "color-*": "initial",
      "font-*": "initial",
      ...Object.fromEntries(colors.map(([name]) => [`color-${name}`, `var(--${name})`])),
      "font-sans": "var(--text-font)",
      "font-mono": "var(--code-font)",
    });
  });

  it("registers design radii with Tailwind and removes default radius utilities", async () => {
    // Use the installed defaults so a missing namespace reset cannot pass unnoticed.
    const defaults = readFileSync(new URL(import.meta.resolve("tailwindcss/theme.css")), "utf8");
    const compiler = await compile(
      `${defaults}\n${css.replace(/^@import .*;$/gm, "")}\n@tailwind utilities;`,
    );
    const output = compiler.build([
      ...Object.keys(radii).map((name) => name.replace("radius-", "rounded-")),
      "rounded-lg",
    ]);
    for (const name of Object.keys(radii)) {
      const utility = name.replace("radius-", "rounded-");
      expect(output).toContain(`.${utility} {\n  border-radius: var(--${name});\n}`);
    }
    expect(output).not.toContain(".rounded-lg {");
    expect(radiusReset).toBe("initial");
    expect(cssRadii).toEqual(radii);
  });

  it("scopes only colors to dark and keeps all theme-independent tokens global", () => {
    expect(Object.keys(dark).sort()).toEqual(colors.map(([name]) => name).sort());
    expect(global).toEqual(
      Object.fromEntries(
        Object.entries(tokens)
          .filter(
            ([name]) =>
              !tsOnly.some((token) => token === name) &&
              !colors.some(([color]) => color === name) &&
              !name.startsWith("radius-"),
          )
          .map(([name, value]) => [name, typeof value === "number" ? String(value) : value]),
      ),
    );
  });

  it("documents failed-text for destructive labels and icons", () => {
    for (const source of [css, ts]) {
      expect(source.match(/Destructive text and icons.*failed-text\./)?.[0]).toBe(
        "Destructive text and icons (Reject, Stop) use failed-text.",
      );
    }
    expect(cssValue(required(dark["failed-text"]))).toBe(tokens["failed-text"]);
    expect(tokens["failed-text"]).toBe("#ff7b7b");
  });

  it("includes the complete DESIGN.md dark color set with its specified values", () => {
    const front = required(required(design.split("---")[1]).split("typography:")[0]);
    const entries = [...front.matchAll(/^ {2}([\w-]+): "([^"\n]+)"$/gm)].filter(
      (match) => !required(match[1]).startsWith("day-"),
    );
    expect(entries.length).toBeGreaterThan(0);
    for (const [, name, value] of entries) {
      expect(tokens[name as keyof typeof tokens], name).toEqual(cssValue(required(value)));
    }
    expect(tokens["dim-node"]).toBe(0.16);
    expect(tokens["dim-module"]).toBe(0.6);
    expect(tokens["quiet-edge"]).toBe(0.45);
  });

  it("loads the documented font weights from the installed packages", () => {
    for (const weight of [400, 600, 700]) {
      expect(css).toContain(
        `@import "@fontsource/atkinson-hyperlegible-next/latin-${String(weight)}.css";`,
      );
    }
    expect(css).toContain('@import "@fontsource/atkinson-hyperlegible-mono/latin-400.css";');
  });

  it("selects the root theme at call time and defaults to dark without a DOM", () => {
    expect(Object.keys(themes)).toEqual(["dark"]);
    expect(getActiveTheme()).toBe(themes.dark);
    const root = { dataset: {} } as HTMLElement;
    expect(getActiveTheme(root)).toBe(themes.dark);
    root.dataset.theme = "dark";
    expect(getActiveTheme(root)).toBe(themes.dark);
    root.dataset.theme = "day";
    expect(() => getActiveTheme(root)).toThrow("Unsupported theme: day");
    root.dataset.theme = "dark";
    expect(getActiveTheme(root)).toBe(themes.dark);
  });
});
