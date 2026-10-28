import { describe, expect, it } from "vitest";

import { sameLights } from "./lights";
import type { FieldLight } from "./lights";

describe("sameLights", () => {
  const light: FieldLight = { id: "a", source: "agent-a", weight: 1, pulses: true };

  it("tells equal selections apart from ones that would draw differently", () => {
    expect(sameLights([light], [{ ...light }])).toBe(true);
    expect(sameLights([light], [])).toBe(false);
    expect(sameLights([light], [{ ...light, weight: 0.72 }])).toBe(false);
    expect(sameLights([light], [{ ...light, pulses: false }])).toBe(false);
    expect(sameLights([light], [{ ...light, source: "agent-b" }])).toBe(false);
    expect(sameLights([light], [{ ...light, id: "b" }])).toBe(false);
    expect(sameLights([light], [{ ...light, marker: "agent-1" }])).toBe(false);
    expect(sameLights([light], [{ ...light, steady: true }])).toBe(false);
  });
});
