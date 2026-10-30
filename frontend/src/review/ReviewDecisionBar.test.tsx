import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { ReviewCard } from "@contracts";
import { CardDecisions } from "./CardDecisions";
import { ReviewDecisionBar } from "./ReviewDecisionBar";
import { ReviewPanel } from "./ReviewPanel";
import { headerCases, headerState } from "./ReviewHeader.fixture";

vi.mock("../editor", () => ({ EditorDiff: () => <div>Inline diff</div> }));
const button = (html: string, name: string) =>
  html.match(new RegExp(`<button[^>]*>(?:(?!</button>).)*${name}</button>`))?.[0] ?? "";
const cards = headerState("a").changeFacts["change-3"]?.at(-1)?.review_cards ?? [];
const render = (answer: ReviewCard["answer"]) =>
  renderToStaticMarkup(
    <ReviewDecisionBar
      changeId="change-3"
      cards={cards.map((card) => ({ ...card, answer }))}
      view={{ kind: "open", failure: null }}
      onDecide={vi.fn()}
    />,
  );

it.each([null, "no", "unsure"] as const)("locks Approve for answer %s", (answer) => {
  const html = render(answer);
  expect(cards.length).toBeGreaterThan(0);
  expect(button(html, "Approve and merge")).toContain('disabled=""');
  expect(button(html, "Approve and merge")).toContain("lucide-lock-keyhole");
  expect(html).toContain("Approve opens when every answer is Yes.");
  expect(html).not.toContain("If main moved");
  expect(html).not.toContain("answered");
});
it("unlocks for all Yes and disables Request changes on an empty comment", () => {
  const html = render("yes");
  expect(button(html, "Approve and merge")).not.toContain('disabled=""');
  expect(button(html, "Approve and merge")).toContain("lucide-git-merge");
  expect(button(html, "Request changes")).toContain('disabled=""');
  expect(html).toContain("If main moved since the agent started");
  expect(html).not.toMatch(/aria-keyshortcuts|Ctrl|Enter|Approve with a click/);
});
it("prefills the comment from No and enables Request changes", () => {
  const html = render("no");
  expect(html).toContain(cards[0]?.question);
  expect(button(html, "Request changes")).not.toContain('disabled=""');
});
it("keeps the unavailable caption separate from status feedback", () => {
  const html = renderToStaticMarkup(
    <ReviewDecisionBar
      changeId="change-4"
      unavailable
      view={{ kind: "pending", action: "approve" }}
    />,
  );
  expect(html).toContain(
    "The check is still unavailable. You decide whether to continue without it.",
  );
  expect(html).toContain("Approving: waiting for the merge result.");
  expect(html).not.toContain("If main moved");
});
it.each(["a", "b", "merged"] as const)("uses the shared bar only for decisions in %s", (name) => {
  const html = renderToStaticMarkup(
    <ReviewPanel
      state={headerState(name)}
      changeId={headerCases[name].changeId}
      onClose={vi.fn()}
      onDecide={vi.fn()}
    />,
  );
  expect(html.match(/review-decision-bar/g) ?? []).toHaveLength(name === "merged" ? 0 : 1);
  expect(html).not.toContain("local-decisions");
});
it("preserves the inbox bar's count, lock copy and hidden Approve on No", () => {
  const html = renderToStaticMarkup(
    <CardDecisions
      cards={cards.map((card) => ({ ...card, answer: "no" }))}
      answers={{}}
      candidate="inbox"
      view={{ kind: "open", failure: null }}
      onDecide={vi.fn()}
    />,
  );
  expect(html).toContain("local-decisions");
  expect(html).toContain("2 of 2 answered");
  expect(html).toContain("Approve opens only when every answer is Yes");
  expect(button(html, "Approve and merge")).toBe("");
});

it("preserves read-only replay comments for cards and omits them in ordinary replay", () => {
  for (const questions of [cards, []]) {
    const html = renderToStaticMarkup(
      <ReviewDecisionBar
        changeId="change-3"
        cards={questions}
        view={{ kind: "recorded", resolution: null }}
      />,
    );
    expect(html.includes("<textarea")).toBe(questions.length > 0);
    if (questions.length) expect(html).toContain('readOnly=""');
  }
});
