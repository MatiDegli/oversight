import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { it } from "vitest";

import { panel, withPage } from "./review.browser.support";

async function emitReview(page: Page) {
  await page.evaluate(() => {
    const proposal = window.reviewTest.state().changes["change-1"]?.proposal;
    if (!proposal) throw new Error("Missing proposal");
    window.reviewTest.emit({
      seq: 0,
      ts: "2026-10-28T12:00:00Z",
      type: "change.reviewed",
      payload: {
        change_id: proposal.change_id,
        revision: proposal.revision,
        head_commit: proposal.head_commit,
        base_commit: proposal.base_commit,
        main_commit: proposal.base_commit,
        candidate_tree: "tree",
        author_model: "fake-scripted",
        reviewer_model: "fake-scripted-b",
        reviewer_provider: "fake",
        reasoning: null,
        verdict: "unknown",
        findings: [],
        complete: false,
        problems: ["review_invalid: /private/worktrees/secret.py"],
      },
    });
  });
}

it("keeps watch and typing focus, then invalidates the note on the person's decision", async () => {
  await withPage(false, async (page) => {
    const open = page.getByRole("button", { name: "Review change" });
    await open.focus();
    await emitReview(page);
    await browserExpect(panel(page)).toHaveCount(0);
    await browserExpect(open).toBeFocused();
    await open.click();
    const note = panel(page).getByRole("region", { name: "Cross-review", exact: true });
    await browserExpect(note).toContainText("Review unavailable");
    await browserExpect(note).not.toContainText("/private");
    const comment = panel(page).getByRole("textbox", { name: /Comment/ });
    await comment.fill("Please cover empty carts");
    await page.evaluate(() => {
      const proposal = window.reviewTest.state().changes["change-1"]?.proposal;
      if (!proposal) throw new Error("Missing proposal");
      window.reviewTest.emit({
        seq: 0,
        ts: "2026-10-28T12:00:01Z",
        type: "error",
        payload: {
          source: "server",
          change_id: proposal.change_id,
          agent_id: proposal.agent_id,
          fatal: false,
          message: `Needs you — revision=${String(proposal.revision)}; head=${proposal.head_commit}; code=review_invalid; reason=/private/worktree`,
        },
      });
    });
    await browserExpect(note).toContainText("Needs you");
    await browserExpect(comment).toBeFocused();
    await page.keyboard.press("Control+Enter");
    await browserExpect(note).toBeVisible();
    await panel(page).getByRole("button", { name: "Request changes", exact: true }).click();
    await browserExpect(note).toHaveCount(0);
  });
});
