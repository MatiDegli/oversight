import { expect, it } from "vitest";
import { truncateTask } from "./truncateTask";

it.each(["", "a", "Keep  whitespace  intact", "x".repeat(47), "x".repeat(48)])(
  "preserves a task of at most 48 characters: %s",
  (task) => {
    expect(truncateTask(task)).toBe(task);
  },
);
it("reserves the 48th character for an ellipsis on an overlong first word", () => {
  expect(truncateTask("x".repeat(49))).toBe(`${"x".repeat(47)}…`);
  expect(truncateTask(`${"x".repeat(48)} trailing words`)).toBe(`${"x".repeat(47)}…`);
});
it("takes the longest whole-word prefix at either side of the boundary", () => {
  const first = "a".repeat(40);
  expect(truncateTask(`${first} second third`)).toBe(`${first} second…`);
  expect(truncateTask(`${first} seconds third`)).toBe(`${first}…`);
  expect(truncateTask(`${first} second  third`)).toBe(`${first} second…`);
  expect(truncateTask(`${first}\tsecond third`)).toBe(`${first}\tsecond…`);
});
it("does not split supplementary characters in an overlong word", () => {
  const result = truncateTask("🛒".repeat(49));
  expect(Array.from(result)).toHaveLength(48);
  expect(result).toBe(`${"🛒".repeat(47)}…`);
});
