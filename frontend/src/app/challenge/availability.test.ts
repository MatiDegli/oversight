import { expect, it } from "vitest";
import { availablePractice } from "./availability";
import { manifest } from "./manifest";
import { recording } from "./recording.fixture";
const real = { kind: "real recorded run" } as const;
it("offers the matching verified recording only in a replay build", () => {
  expect(availablePractice(true, real, recording)?.cut.lastSeq).toBe(63);
  expect(availablePractice(false, real, recording)).toBeNull();
});
it("withholds practice for a handwritten source even with a matching receipt", () => {
  expect(availablePractice(true, { kind: "hand-written demo fixture" }, recording) === null).toBe(
    true,
  );
});
it("withholds practice instead of throwing for a mismatched manifest", () => {
  expect(
    availablePractice(true, real, recording, {
      ...manifest,
      recording: { ...manifest.recording, sha256: "0".repeat(64) },
    }),
  ).toBeNull();
});
