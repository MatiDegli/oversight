import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

it("preserves the authorized synthetic capture zoom at 1280", () => {
  // The coordinator froze this camera setting; emitter framing has its own shared step.
  const source = readFileSync(new URL("./mapLines.browser.test.ts", import.meta.url), "utf8");
  const targets = [
    ...source.matchAll(/if \(!boltons && width === 1280\) await zoomTo\(page, ([\d.]+)\);/g),
  ];
  expect(targets.map((match) => Number(match[1]))).toEqual([0.25]);
});
