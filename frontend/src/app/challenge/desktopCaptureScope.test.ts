import { createHash } from "node:crypto";
import { expect, it } from "vitest";
import captureSource from "../../../shots/capture.ts?raw";
import scenesSource from "../../../shots/scenes.ts?raw";

// The phone practice must leave the shared desktop capture pipeline byte-identical to e72fcc7.
// Pin the whole files so scene waits and motion overrides cannot silently change that baseline.
it("preserves the desktop screenshot options from the approved base", () => {
  expect(createHash("sha256").update(captureSource).digest("hex")).toBe(
    "80cbeb165614a9035b16741f418c17cc422a71929d7ac095fa87806b0774dbf6",
  );
});

it("preserves the desktop scene timing and media settings from the approved base", () => {
  expect(createHash("sha256").update(scenesSource).digest("hex")).toBe(
    "4e9c586eaa224e8da4fce7a8478b361545a9e20ff118cfeb59b51c2d55d5855e",
  );
});
