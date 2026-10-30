import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";

import ts from "typescript";
import { expect, it } from "vitest";

const fixture = new URL("./dotField.browser.test.ts", import.meta.url);
const source = readFileSync(fixture, "utf8");

/** Collect the real browser checks without launching Chromium or the fixture's Vite server. */
function checks(lifeShots: string | undefined, referenceExists: boolean) {
  const registered = new Map<string, boolean>();
  const register = (enabled: boolean) => (name: string) => registered.set(name, enabled);
  const browserIt = Object.assign(register(true), {
    runIf: register,
    each: () => register(true),
  });
  const require = createRequire(import.meta.url);
  const compiled = ts.transpileModule(
    source.replaceAll("import.meta.url", JSON.stringify(fixture.href)),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } },
  ).outputText;
  runInNewContext(compiled, {
    exports: {},
    URL,
    process: { env: { LIFE_SHOTS: lifeShots } },
    require: (name: string) => {
      if (name === "vitest") return { it: browserIt, beforeAll() {}, afterAll() {} };
      if (name === "node:fs") return { existsSync: () => referenceExists };
      if (name === "@playwright/test" || name === "vite") return {};
      if (name === "node:url") return require(name) as unknown;
      throw new Error(`Unexpected fixture dependency: ${name}`);
    },
  });
  return registered;
}

const captures = "captures rest and busy at 1920 by 1080, dark and day, beside their sketches";
const pairs = "pairs busy captures with the optional interactive reference";
const fade = "checks the fade curve at 1920 by 1080";
const histograms = "checks busy lamp histograms in both themes at 1920 by 1080";

it.each([undefined, "", "0", "true"])(
  "keeps visual assertions active and capture work off with LIFE_SHOTS=%s",
  (value) => {
    const registered = checks(value, true);
    expect(registered.get(captures)).toBe(false);
    expect(registered.get(pairs)).toBe(false);
    expect(registered.get(fade)).toBe(true);
    expect(registered.get(histograms)).toBe(true);
  },
);

it.each([false, true])("only reference pairs need the local reference: exists=%s", (exists) => {
  const registered = checks("1", exists);
  expect(registered.get(captures)).toBe(true);
  expect(registered.get(pairs)).toBe(exists);
  expect(registered.get(fade)).toBe(true);
  expect(registered.get(histograms)).toBe(true);
});

it("keeps the consolidated small_cone decision without resurrecting its replaced entry", () => {
  const decisions = readFileSync(new URL("../../../DECISIONS.md", import.meta.url), "utf8");
  const headings = decisions.split("\n").filter((line) => line.startsWith("## "));
  expect(
    headings.filter((line) => line.endsWith("small_cone does not pass what it cannot see")),
  ).toHaveLength(1);
  expect(
    headings.filter((line) => line.endsWith("Small cones require no unobserved possible callers")),
  ).toHaveLength(0);
  expect(decisions).toContain("Audit note: commit 13c08bc replaced the same-day entry");
});
