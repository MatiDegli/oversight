import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { PracticePanel, freshAttempt } from "./PracticePanel";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";
const practice = validateManifest(manifest, recording);
const handlers = {
  onAnswer: vi.fn(),
  onStart: vi.fn(),
  onNext: vi.fn(),
  onReveal: vi.fn(),
  onRestart: vi.fn(),
  onExit: vi.fn(),
};
it("withholds historical findings until reveal; uses only recorded proposal facts", () => {
  const attempt = { ...freshAttempt(), started: true };
  const before = renderToStaticMarkup(
    <PracticePanel practice={practice} attempt={attempt} {...handlers} />,
  );
  expect(before).toMatch(/disabled=""[^>]*>Show findings/);
  expect(before).not.toMatch(/Recorded decision:|Matches with recorded/);
  const answers = { "change-2": "Reject", "change-1": "Approve" } as const;
  const revealed = renderToStaticMarkup(
    <PracticePanel
      practice={practice}
      attempt={{ ...attempt, answers, revealed: true }}
      {...handlers}
    />,
  );
  expect(revealed).toContain("Matches with recorded decisions: 1/2.");
  expect(revealed).toContain("Your practice answer: Reject");
  expect(revealed).toContain("Recorded decision: Approve");
  expect(revealed).toContain("29 passing tests");
  expect(revealed).toContain("32 passing tests");
  expect(revealed).toContain("0 possibly affected entries");
  expect(revealed).toContain("9 possibly affected entries");
  expect(revealed).toContain(
    "Detailed review facts, cross-review and behavior evidence were not recorded.",
  );
  expect(revealed).not.toMatch(/Behavioral example|Measured result|Clean review|Safe to merge/);
  expect(Object.values(handlers).every((handler) => handler.mock.calls.length === 0)).toBe(true);
});
