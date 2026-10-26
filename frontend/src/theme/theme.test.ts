import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import * as exportedTokens from "./index";

const css = readFileSync(new URL("./index.css", import.meta.url), "utf8");

// Adding an export requires a naming rule; no export can silently escape parity checks.
const tokenNames: Record<keyof typeof exportedTokens, (name: string) => string> = {
  colors: (name) => name,
  stateColors: (name) => name,
  agentColors: (slot) => `agent-${slot.toLowerCase()}`,
  fontFamilies: (name) => {
    if (name === "sans") return "text-font";
    if (name === "mono") return "code-font";
    throw new Error(`Unknown font token: ${name}`);
  },
};

function collectTokens(): Record<string, string> {
  expect(Object.keys(tokenNames).sort()).toEqual(Object.keys(exportedTokens).sort());
  const entries: [string, string][] = [];
  for (const name of Object.keys(exportedTokens) as (keyof typeof exportedTokens)[]) {
    for (const [key, value] of Object.entries(exportedTokens[name])) {
      entries.push([tokenNames[name](key), value]);
    }
  }
  const keys = entries.map(([key]) => key);
  expect(new Set(keys).size, "Token names must be unique across exports").toBe(keys.length);
  return Object.fromEntries(entries);
}

function declarations(block: string): Record<string, string> {
  const entries: [string, string][] = [];
  for (const match of block.matchAll(/--([\w*-]+)\s*:\s*([^;]+);/g)) {
    const [, name, value] = match;
    if (name === undefined || value === undefined) throw new Error("Invalid CSS declaration");
    entries.push([name, value.trim()]);
  }
  return Object.fromEntries(entries);
}

describe("theme", () => {
  it("keeps every CSS token equal to its TypeScript constant", () => {
    const root = /:root\s*\{([^}]+)\}/.exec(css)?.[1] ?? "";
    expect(declarations(root)).toEqual(collectTokens());
  });

  it("maps every token to a Tailwind utility", () => {
    const theme = /@theme inline\s*\{([^}]+)\}/.exec(css)?.[1] ?? "";
    const tokens = collectTokens();
    const expected = Object.fromEntries(
      Object.keys(tokens).map((token) => [`color-${token}`, `var(--${token})`]),
    );
    delete expected["color-text-font"];
    delete expected["color-code-font"];

    expect(declarations(theme)).toEqual({
      ...expected,
      "color-*": "initial",
      "font-*": "initial",
      "font-sans": "var(--text-font)",
      "font-mono": "var(--code-font)",
    });
  });
});
