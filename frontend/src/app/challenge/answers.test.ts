import { expect, it } from "vitest";
import { answerOnce, complete, freshAttempt, score } from "./PracticePanel";
import type { Answer } from "./PracticePanel";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";
const practice = validateManifest(manifest, recording);
it.each<[Answer, Answer, number]>([
  ["Approve", "Approve", 2],
  ["Approve", "Reject", 1],
  ["Reject", "Approve", 1],
  ["Reject", "Reject", 0],
])("%s/%s matches %i recorded decisions", (a, b, expected) => {
  let attempt = freshAttempt();
  expect(complete(practice, attempt)).toBe(false);
  attempt = answerOnce(attempt, "change-2", a);
  expect(complete(practice, attempt)).toBe(false);
  expect(answerOnce(attempt, "change-2", b)).toBe(attempt);
  expect(attempt.index).toBe(0);
  attempt = answerOnce(attempt, "change-1", b);
  expect(complete(practice, attempt)).toBe(true);
  expect(score(practice, attempt)).toBe(expected);
  expect(score(practice, { ...attempt, revealed: true })).toBe(expected);
  expect(freshAttempt()).toEqual({ index: 0, started: false, revealed: false, answers: {} });
});
