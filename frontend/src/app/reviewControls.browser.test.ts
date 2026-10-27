import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import { panel, withPage } from "./review.browser.support";

const modifiedInput = (page: Page) =>
  panel(page).locator(".editor.modified :is(.native-edit-context, textarea.inputarea)");
const reviewFile = (page: Page) => panel(page).getByLabel("Editable review file");
async function open(page: Page) {
  await page.getByRole("button", { name: "Review change" }).click();
  await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "clean");
}
async function typeEdit(page: Page, text: string) {
  await modifiedInput(page).focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(text);
}

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
      await page.getByRole("button", { name: "Review change" }).click();
      await browserExpect(reviewFile(page)).toHaveAttribute("data-state", "dirty");
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

it.each([4, 20])(
  "Stop asks first on the agent card at seq %i and waits for SSE without moving focus",
  async (seq) => {
    await withPage(
      false,
      async (page) => {
        let calls = 0;
        await page.route("**/agents/agent-1/stop", async (route) => {
          calls++;
          await route.fulfill({ status: 202 });
        });
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
