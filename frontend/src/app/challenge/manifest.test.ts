import { describe, expect, it } from "vitest";
import { manifest, validateManifest } from "./manifest";

import { recording } from "./recording.fixture";

describe("practice manifest", () => {
  it("binds the actual two proposals and their recorded approvals", () => {
    const bound = validateManifest(manifest, recording);
    expect(bound.cut.lastSeq).toBe(63);
    expect(
      bound.cases.map(({ proposal }) => [
        proposal.change_id,
        proposal.tests.passed,
        proposal.impact.entries.length,
      ]),
    ).toEqual([
      ["change-2", 29, 0],
      ["change-1", 32, 9],
    ]);
    expect(Object.values(bound.cut.changes).every((change) => change.resolution === null)).toBe(
      true,
    );
  });
  it.each([
    "run",
    "digest",
    "duplicate",
    "unknown",
    "revision",
    "head",
    "cut",
    "evidence",
    "resolution",
    "three",
    "gap",
  ])("rejects %s mismatch", (fault) => {
    const candidate = structuredClone(manifest);
    const source = structuredClone(recording);
    const first = candidate.cases[0];
    if (!first) throw new Error("Missing first case");
    if (fault === "run") source.provenance.run_id = "run-other";
    if (fault === "digest") source.provenance.sha256 = "0".repeat(64);
    if (fault === "duplicate") candidate.cases[1] = first;
    if (fault === "unknown") first.changeId = "unknown";
    if (fault === "revision") first.revision++;
    if (fault === "head") first.head = "other";
    if (fault === "cut") candidate.cutSeq = 55;
    if (fault === "evidence") first.evidenceSeqs = [64];
    if (fault === "resolution") first.resolutionSeq = 64;
    if (fault === "three") candidate.edition = "three-change";
    if (fault === "gap")
      source.text = source.text
        .split("\n")
        .filter((_, i) => i !== 20)
        .join("\n");
    expect(() => validateManifest(candidate, source)).toThrow();
  });
});
