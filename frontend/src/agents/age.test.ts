import { describe, expect, it } from "vitest";

import { ageWords } from "./age";

const at = (offsetSeconds: number) =>
  new Date(Date.UTC(2026, 9, 27, 14, 0, offsetSeconds)).toISOString();
const now = at(0);

describe("ageWords", () => {
  it.each([
    [0, "now"],
    [4, "now"],
    [5, "5 s ago"],
    [40, "40 s ago"],
    [59, "59 s ago"],
    [60, "1 min ago"],
    [119, "1 min ago"],
    [120, "2 min ago"],
    [3599, "59 min ago"],
    [3600, "1 h ago"],
    [7300, "2 h ago"],
  ])("reads %s seconds as %s", (seconds, words) => {
    expect(ageWords(at(-seconds), now)).toBe(words);
  });

  it("says nothing without both times or with one it cannot read", () => {
    expect(ageWords(undefined, now)).toBe("");
    expect(ageWords(at(-5), null)).toBe("");
    expect(ageWords("not a time", now)).toBe("");
  });

  it("never reads a step from the future as a negative age", () => {
    expect(ageWords(at(30), now)).toBe("now");
  });
});
