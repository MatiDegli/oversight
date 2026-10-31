import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import { panel, withPage } from "./review.browser.support";

const modifiedInput = (page: Page) =>
  panel(page).locator(
    ".review-diff-viewport:not([hidden]) .editor.modified :is(.native-edit-context, textarea.inputarea)",
  );
const reviewFile = (page: Page) =>
  panel(page).locator(".review-diff-viewport:not([hidden])").getByLabel("Editable review file");
async function open(page: Page) {
  await page.getByRole("button", { name: "Review change" }).click();
  await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "clean");
}
async function typeEdit(page: Page, text: string) {
  await modifiedInput(page).focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(text);
}

it("blocks decisions during editor loading without claiming there are review edits", async () => {
  await withPage(false, async (page) => {
    let release!: () => void;
    const waiting = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/src/editor/implementation.ts*", async (route) => {
      await waiting;
      await route.continue();
    });
    try {
      await page.evaluate(() => {
        window.reviewTest.holdFileReads();
      });
      await page.getByRole("button", { name: "Review change" }).click();
      await browserExpect(panel(page).getByText("Loading editor…")).toBeVisible();
      const approve = panel(page).getByRole("button", { name: "Approve and merge" });
      const request = panel(page).getByRole("button", { name: "Request changes", exact: true });
      const comment = panel(page).getByRole("textbox", { name: /Comment/ });
      const editsNotice = panel(page).getByText(
        "Save or discard your review edits before approving or requesting changes.",
      );
      await comment.fill("Cover empty carts");
      await browserExpect(approve).toBeDisabled();
      await browserExpect(request).toBeDisabled();
      await browserExpect(editsNotice).toHaveCount(0);
      await browserExpect(
        panel(page).getByRole("button", { name: "Reject", exact: true }),
      ).toBeEnabled();
      release();
      // Wait for the cold editor chunk (implementation.ts and Monaco) after release().
      await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "loading", {
        timeout: 15_000,
      });
      await browserExpect(approve).toBeDisabled();
      await browserExpect(request).toBeDisabled();
      await browserExpect(editsNotice).toHaveCount(0);
      await browserExpect(comment).toBeFocused();
      await page.evaluate(() => {
        window.reviewTest.releaseFileReads();
      });
      await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "clean");
      await browserExpect(approve).toBeEnabled();
      await browserExpect(request).toBeEnabled();
      await browserExpect(comment).toBeFocused();
      await browserExpect(editsNotice).toHaveCount(0);

      await typeEdit(page, "\n# person edit\n");
      await browserExpect(editsNotice).toBeVisible();
      await browserExpect(approve).toBeDisabled();
      await browserExpect(request).toBeDisabled();
      await panel(page).getByRole("button", { name: "Discard edits", exact: true }).click();
      await browserExpect(editsNotice).toHaveCount(0);
      await browserExpect(approve).toBeEnabled();
      await browserExpect(request).toBeEnabled();
    } finally {
      release();
      await page.unrouteAll({ behavior: "wait" });
    }
  });
});

it("does not claim review edits while a newly selected file loads", async () => {
  await withPage(false, async (page) => {
    await open(page);
    await page.evaluate(() => {
      const change = window.reviewTest.state().changes["change-1"];
      const file = change?.files["shop/pricing.py"]?.file;
      const proposal = change?.proposal;
      const stat = proposal?.files[0];
      if (!file || !proposal || !stat) throw new Error("Missing proposal file");
      window.reviewTest.emit({
        seq: 0,
        ts: "2026-10-27T14:04:00Z",
        type: "change.updated",
        payload: {
          change_id: proposal.change_id,
          agent_id: proposal.agent_id,
          author: "agent",
          file: { ...file, path: "shop/orders.py" },
          node_ids: [],
          graph: proposal.graph,
          files_changed: 2,
          lines_added: proposal.lines_added,
          lines_removed: proposal.lines_removed,
        },
      });
      window.reviewTest.emit({
        seq: 0,
        ts: "2026-10-27T14:04:01Z",
        type: "change.proposed",
        payload: { ...proposal, files: [...proposal.files, { ...stat, path: "shop/orders.py" }] },
      });
      window.reviewTest.holdFileReads();
    });
    const comment = panel(page).getByRole("textbox", { name: /Comment/ });
    await comment.fill("Cover empty carts");
    const fileButton = panel(page).getByRole("button", { name: /shop\/orders.py/ });
    await fileButton.click();
    await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "loading");
    const approve = panel(page).getByRole("button", { name: "Approve and merge" });
    const request = panel(page).getByRole("button", { name: "Request changes", exact: true });
    await browserExpect(approve).toBeDisabled();
    await browserExpect(request).toBeDisabled();
    await browserExpect(panel(page).getByText(/Save or discard your review edits/)).toHaveCount(0);
    await page.evaluate(() => {
      window.reviewTest.releaseFileReads();
    });
    await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "clean");
    await browserExpect(approve).toBeEnabled();
    await browserExpect(request).toBeEnabled();
    await browserExpect(fileButton).toBeFocused();
  });
});

it.each(["merge", "tests"] as const)(
  "allows Request changes after a %s conflict",
  async (kind) => {
    await withPage(false, async (page) => {
      await open(page);
      const request = panel(page).getByRole("button", { name: "Request changes", exact: true });
      const approve = panel(page).getByRole("button", { name: "Approve and merge" });
      const reject = panel(page).getByRole("button", { name: "Reject", exact: true });
      const comment = panel(page).getByRole("textbox", { name: /Comment/ });
      await comment.fill("Cover empty carts");
      await browserExpect(request).toBeEnabled();
      await approve.click();
      await browserExpect(request).toBeDisabled();
      await page.evaluate((kind) => {
        window.reviewTest.emit({
          seq: 0,
          ts: "2026-10-27T14:04:00Z",
          type: "change.resolved",
          payload: {
            review_seq: null,
            requested_by: null,
            head_commit: null,
            change_id: "change-1",
            agent_id: "agent-1",
            revision: 1,
            decision: "approved",
            comment: "Cover empty carts",
            merge: "conflict",
            merge_commit: null,
            conflict:
              kind === "merge"
                ? { kind, paths: ["shop/pricing.py"], tests: null }
                : {
                    kind,
                    paths: [],
                    tests: {
                      cases: null,
                      cases_complete: null,
                      outcome: "failed",
                      passed: 27,
                      failed: 1,
                      skipped: 0,
                      duration_ms: 100,
                      failed_tests: ["test_combined_discount"],
                    },
                  },
            reason: null,
            decided_by: null,
            reasons: [],
            assessment: null,
            answers: null,
          },
        });
      }, kind);
      await browserExpect(panel(page).getByText(/Approved, not merged/)).toBeVisible();
      await browserExpect(approve).toBeDisabled();
      await browserExpect(request).toBeEnabled();
      await browserExpect(reject).toBeEnabled();
      await comment.fill("   ");
      await browserExpect(request).toBeDisabled();
      await comment.fill("Try again on the new base");
      await browserExpect(comment).toBeFocused();
      await browserExpect(request).toBeEnabled();
      await request.focus();
      await page.keyboard.press("Enter");
      expect(await page.evaluate(() => window.reviewTest.decisions)).toMatchObject([
        { action: "approve" },
        { action: "request-changes", comment: "Try again on the new base" },
      ]);
      await browserExpect(request).toBeDisabled();
    });
  },
  30_000,
);

it("requires a comment, stays pending until SSE, and reopens the same change after revision", async () => {
  await withPage(false, async (page) => {
    await open(page);
    const request = panel(page).getByRole("button", { name: "Request changes", exact: true });
    const comment = panel(page).getByRole("textbox", { name: /Comment/ });
    await browserExpect(request).toBeDisabled();
    await comment.fill("   ");
    await browserExpect(request).toBeDisabled();
    await comment.fill("Cover empty carts");
    await page.keyboard.press("Enter");
    await page.keyboard.press("Control+Enter");
    expect(await page.evaluate(() => window.reviewTest.decisions)).toEqual([]);
    await browserExpect(comment).toBeFocused();
    await request.click();
    await browserExpect(request).toBeDisabled();
    await browserExpect(
      panel(page).getByText("Requesting changes: waiting for the agent."),
    ).toBeVisible();
    const decisions = await page.evaluate(() => window.reviewTest.decisions);
    expect(decisions[0]?.action).toBe("request-changes");
    expect(decisions[0]?.comment.trim()).toBe("Cover empty carts");
    await page.evaluate(() => {
      const agent = window.reviewTest.state().agents["agent-1"];
      if (!agent) throw new Error("Missing agent");
      window.reviewTest.emit({
        seq: 0,
        ts: "2026-10-27T14:04:00Z",
        type: "change.resolved",
        payload: {
          review_seq: null,
          requested_by: null,
          head_commit: null,
          change_id: "change-1",
          agent_id: agent.agent_id,
          revision: 1,
          decision: "changes_requested",
          comment: "Cover empty carts",
          merge: null,
          merge_commit: null,
          conflict: null,
          reason: null,
          decided_by: null,
          reasons: [],
          assessment: null,
          answers: null,
        },
      });
      window.reviewTest.emit({
        seq: 0,
        ts: "2026-10-27T14:04:01Z",
        type: "agent.status",
        payload: { ...agent, state: "revising" },
      });
    });
    await browserExpect(
      panel(page).getByText("Changes requested: the comment went back to the agent"),
    ).toBeVisible();
    await browserExpect(
      panel(page).getByRole("button", { name: "Approve and merge" }),
    ).toBeDisabled();
    await page.evaluate(() => {
      const state = window.reviewTest.state();
      const proposal = state.changes["change-1"]?.proposal;
      const agent = state.agents["agent-1"];
      if (!proposal || !agent) throw new Error("Missing proposal");
      window.reviewTest.emit({
        seq: 0,
        ts: "2026-10-27T14:04:02Z",
        type: "change.proposed",
        payload: { ...proposal, revision: 2 },
      });
      window.reviewTest.emit({
        seq: 0,
        ts: "2026-10-27T14:04:03Z",
        type: "agent.status",
        payload: { ...agent, state: "ready_for_review" },
      });
    });
    await browserExpect(request).toBeEnabled();
    await browserExpect(
      panel(page).getByRole("button", { name: "Approve and merge" }),
    ).toBeEnabled();
    expect(await comment.inputValue()).toContain("Cover empty carts");
  });
}, 30_000);

it("edits only the new side, saves with its base hash and agent id, and keeps text on errors", async () => {
  await withPage(
    false,
    async (page) => {
      const saves: Record<string, unknown>[] = [];
      let failure = "stale_file";
      await page.route("**/files", async (route) => {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        saves.push(body);
        if (failure)
          await route.fulfill({
            status: 409,
            json: {
              error: { code: failure, message: "The agent is not ready. Wait and try Save again." },
            },
          });
        else await route.fulfill({ json: { path: body.path, hash: "a".repeat(64) } });
      });
      await open(page);
      const save = panel(page).getByRole("button", { name: "Save review edits" });
      await browserExpect(save).toBeDisabled();
      await typeEdit(page, "\n# person edit\n");
      await browserExpect(save).toBeEnabled();
      await browserExpect(
        panel(page).getByRole("button", { name: "Approve and merge" }),
      ).toBeDisabled();
      await save.click();
      await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "stale");
      await browserExpect(
        reviewFile(page)
          .getByText(/Your edits are still here/)
          .first(),
      ).toBeVisible();
      await modifiedInput(page).focus();
      await page.keyboard.press("Control+End");
      await browserExpect(panel(page).locator(".editor.modified .view-lines")).toContainText(
        "# person edit",
      );
      expect(saves[0]).toMatchObject({
        path: "shop/pricing.py",
        agent_id: "agent-1",
      });
      expect(saves[0]?.base_hash).toMatch(/^[a-f0-9]{64}$/);
      // Closing and reopening retains the stale draft and its original hash.
      await page.getByRole("button", { name: "Close review" }).click();
      await page.evaluate(() => {
        window.reviewTest.holdFileReads();
      });
      await page.getByRole("button", { name: "Review change" }).click();
      await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "loading");
      await browserExpect(panel(page).getByText(/Save or discard your review edits/)).toBeVisible();
      await page.evaluate(() => {
        window.reviewTest.releaseFileReads();
      });
      await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "dirty");
      await browserExpect(panel(page).getByText(/Save or discard your review edits/)).toBeVisible();
      await browserExpect(
        panel(page).getByRole("button", { name: "Approve and merge" }),
      ).toBeDisabled();
      failure = "wrong_state";
      await save.click();
      await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "failed");
      await browserExpect(reviewFile(page).getByRole("alert")).toContainText(
        "The agent is not ready",
      );
      await modifiedInput(page).focus();
      await page.keyboard.press("Control+End");
      await browserExpect(panel(page).locator(".editor.modified .view-lines")).toContainText(
        "# person edit",
      );
      failure = "";
      await save.click();
      await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "clean");
      expect(saves).toHaveLength(3);
      expect(saves[2]).toEqual(saves[0]);
      await browserExpect(save).toBeDisabled();
      await browserExpect(
        panel(page).getByRole("button", { name: "Approve and merge" }),
      ).toBeEnabled();
      // An event may make the agent busy while the panel stays open: the modified side locks.
      await page.evaluate(() => {
        const agent = window.reviewTest.state().agents["agent-1"];
        if (!agent) throw new Error("Missing agent");
        window.reviewTest.emit({
          seq: 0,
          ts: "2026-10-27T14:04:00Z",
          type: "agent.status",
          payload: { ...agent, state: "revising" },
        });
      });
      await browserExpect(
        panel(page).getByText("Read-only while the agent is not ready"),
      ).toBeVisible();
      await typeEdit(page, "MUST_NOT_APPEAR");
      await browserExpect(panel(page).locator(".editor.modified .view-lines")).not.toContainText(
        "MUST_NOT_APPEAR",
      );
    },
    20,
    Array<string>(2).fill(
      "Failed to load resource: the server responded with a status of 409 (Conflict)",
    ),
  );
}, 30_000);

it.each([
  { seq: 4, state: "working" },
  { seq: 20, state: "ready_for_review" },
  { seq: 4, state: "halted" },
] as const)(
  "Stop asks first on the $state card and waits for SSE without moving focus",
  async ({ seq, state }) => {
    await withPage(
      false,
      async (page) => {
        let calls = 0;
        await page.route("**/agents/agent-1/stop", async (route) => {
          calls++;
          await route.fulfill({ status: 202 });
        });
        if (state === "halted") {
          expect(
            await page.evaluate(() => window.reviewTest.state().changes["change-1"]?.proposal),
          ).toBeNull();
          await page.evaluate(() => {
            const agent = window.reviewTest.state().agents["agent-1"];
            if (!agent) throw new Error("Missing agent");
            window.reviewTest.emit({
              seq: 0,
              ts: "2026-10-27T14:04:00Z",
              type: "agent.status",
              payload: { ...agent, state: "halted", detail: "Could not propose the change" },
            });
          });
          await browserExpect(page.getByText("Could not propose the change")).toBeVisible();
          await browserExpect(page.getByRole("button", { name: "Review change" })).toHaveCount(0);
        }
        const stop = page.getByRole("button", { name: "Stop and discard", exact: true });
        await stop.click();
        expect(calls).toBe(0);
        await browserExpect(stop).toBeFocused();
        await page.getByRole("button", { name: "Cancel", exact: true }).click();
        expect(calls).toBe(0);
        await stop.click();
        const confirm = page.getByRole("button", { name: "Confirm stop and discard" });
        await confirm.click();
        await browserExpect.poll(() => calls).toBe(1);
        await browserExpect(stop).toBeDisabled();
        await browserExpect(
          page.getByText("Stopping: waiting for the discarded change."),
        ).toBeVisible();
        await page.evaluate(() => {
          const agent = window.reviewTest.state().agents["agent-1"];
          if (!agent) throw new Error("Missing agent");
          window.reviewTest.emit({
            seq: 0,
            ts: "2026-10-27T14:04:00Z",
            type: "agent.status",
            payload: { ...agent, state: "stopped" },
          });
        });
        await browserExpect(stop).toHaveCount(0);
        await browserExpect(panel(page)).toHaveCount(0);
        await browserExpect(page.getByRole("article", { name: "Agent A" })).toBeFocused();
      },
      seq,
    );
  },
  30_000,
);

it("keeps dirty review edits blocking decisions while the diff is collapsed", async () => {
  await withPage(false, async (page) => {
    await open(page);
    await typeEdit(page, "\n# person draft\n");
    const review = panel(page);
    await review.getByRole("textbox", { name: /Comment/ }).fill("Keep my draft");
    await review.getByRole("button", { name: /shop\/pricing.py/ }).click();
    await browserExpect(review.getByRole("button", { name: "Approve and merge" })).toBeDisabled();
    await browserExpect(
      review.getByRole("button", { name: "Request changes", exact: true }),
    ).toBeDisabled();
    await review.getByRole("button", { name: /shop\/pricing.py/ }).click();
    await browserExpect(review.getByLabel("Editable review file")).toHaveAttribute(
      "data-state",
      "dirty",
    );
    await review.getByRole("button", { name: "Discard edits", exact: true }).click();
    await browserExpect(review.getByRole("button", { name: "Approve and merge" })).toBeEnabled();
    await browserExpect(
      review.getByRole("button", { name: "Request changes", exact: true }),
    ).toBeEnabled();
  });
});
