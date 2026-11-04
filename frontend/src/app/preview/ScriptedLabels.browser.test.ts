import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";
import { chatEvents } from "./previewChat.browser.support";
import {
  capture,
  matrix,
  scriptedLabelsSupport,
  selectScene,
} from "./scriptedLabels.browser.support";

const support = scriptedLabelsSupport();
const label = "Scripted demo agents";
const explanation = "Entered text does not determine scripted actions.";
it.each([".conversation-entries", ".chat-change-card", ".conversation-composer"])(
  "refuses a coder capture without %s",
  async (selector) => {
    const session = await support.session();
    try {
      await selectScene(session, "coder");
      await check(session.page.locator(selector)).toBeVisible();
      await session.page.locator(selector).evaluate((node) => node.remove());
      await expect(capture(session, "coder")).rejects.toThrow();
    } finally {
      await session.close();
    }
  },
);
it.each(matrix)(
  "shows scripted $scene at $viewport.width in $theme",
  async ({ scene, viewport, theme }) => {
    const session = await support.session(viewport, theme);
    try {
      const row = await selectScene(session, scene);
      await capture(session, scene);
      if (process.env.SCRIPTED_LABELS_CAPTURE === "before") return;
      await check(row.getByText(label, { exact: true })).toBeVisible();
      const header = session.page.locator(".conversation-header");
      await check(header.getByText(label, { exact: true })).toBeVisible();
      await check(header.getByText(explanation, { exact: true })).toBeVisible();
      if (scene === "coder") {
        const model = await row.locator(".chat-agent-model").boundingBox();
        const provenance = await row.getByText(label, { exact: true }).boundingBox();
        if (!model || !provenance) throw new Error("Missing coder model or provenance");
        expect(provenance.y - (model.y + model.height)).toBe(6);
      } else {
        const model = await header
          .getByText("Lead B · Scripted fake model", { exact: true })
          .boundingBox();
        const provenance = await header.getByText(label, { exact: true }).boundingBox();
        if (!model || !provenance) throw new Error("Missing lead model or provenance");
        expect(provenance.y).toBeGreaterThanOrEqual(model.y + model.height);
      }
      expect(
        await header
          .locator(".scripted-agent-provenance")
          .evaluate((node) => getComputedStyle(node).gap),
      ).toBe("6px");
      expect(session.pageErrors).toEqual([]);
    } finally {
      await session.close();
    }
  },
);
it("omits scripted provenance for the same events with Muse profiles", async () => {
  const session = await support.session(undefined, "dark", true);
  try {
    for (const scene of ["coder", "lead"]) {
      await selectScene(session, scene);
      await check(session.page.getByText(label, { exact: true })).toHaveCount(0);
      await check(session.page.getByText(explanation, { exact: true })).toHaveCount(0);
    }
    expect(session.pageErrors).toEqual([]);
  } finally {
    await session.close();
  }
});
it("retains focus and drafts across selection and status, and opens and closes the shop", async () => {
  const session = await support.session();
  const { page } = session;
  try {
    await selectScene(session, "lead");
    const composer = page.locator(".conversation-composer textarea");
    await composer.fill("Keep this scripted lead draft");
    await selectScene(session, "coder");
    const coder = page.locator('[data-agent-origin="agent-1"]');
    await check(coder).toBeFocused();
    await selectScene(session, "lead");
    await check(composer).toHaveValue("Keep this scripted lead draft");
    await composer.focus();
    const status = chatEvents().findLast((event) => event.type === "lead.status");
    if (status?.type !== "lead.status") throw new Error("Missing lead status");
    session.append({ ...status, payload: { ...status.payload, revision: 3, state: "working" } });
    await check(page.locator(".lead-header")).toContainText("Working");
    await check(composer).toBeFocused();
    await check(composer).toHaveValue("Keep this scripted lead draft");
    await page.getByRole("button", { name: "Preview app", exact: true }).click();
    await check(page.locator(".preview-panel-phase")).toContainText("App starting");
    // Starting also renders before the POST receipt; wait for committed fixture details.
    await check(page.locator(".preview-panel-provenance summary")).toHaveText("Revision 1");
    session.publish("ready");
    await check(page.locator(".preview-panel-state")).toHaveText("Ready");
    await check(
      page.frameLocator("iframe").getByRole("heading", { name: "Candidate shop" }),
    ).toBeVisible();
    await page.locator(".preview-panel button").click();
    await check(page.locator("iframe")).toHaveCount(0);
    await check(composer).toHaveValue("Keep this scripted lead draft");
    expect(session.pageErrors).toEqual([]);
  } finally {
    await session.close();
  }
});

it("retains scripted provenance for a folded member of a mixed team at 390", async () => {
  const session = await support.session({ width: 390, height: 844 }, "day", "mixed");
  const { page } = session;
  try {
    const status = chatEvents().find((event) => event.type === "agent.status");
    if (status?.type !== "agent.status") throw new Error("Missing coder status");
    session.append({
      type: "plan.updated",
      seq: 0,
      ts: status.ts,
      payload: {
        plan_id: "mixed-plan",
        started_by: "agent-900001",
        revision: 1,
        task: "Fixture mixed team",
        graph_version: "fixture",
        state: "running",
        detail: null,
        queued_task_id: null,
        subtasks: [
          {
            agent_id: "agent-1",
            task: status.payload.task,
            zone: { paths: ["a.py"], node_ids: [] },
            depends_on: [],
            state: "running",
          },
        ],
      },
    });
    session.append({ ...status, payload: { ...status.payload, plan_id: "mixed-plan" } });
    const coder = await selectScene(session, "coder");
    await check(coder).toHaveAttribute("data-member", "true");
    await check(coder.getByText(label, { exact: true })).toBeVisible();
    await check(page.locator(".chat-lead-row").getByText(label, { exact: true })).toHaveCount(0);
    const disclosure = page.getByRole("button", { name: /^Members of Lead/ });
    await disclosure.click();
    await check(coder).toBeHidden();
    await check(
      page.locator(".conversation-header").getByText(label, { exact: true }),
    ).toBeVisible();
    await check(
      page.locator(".conversation-header").getByText(explanation, { exact: true }),
    ).toBeVisible();
    await check(disclosure).toBeFocused();
    await disclosure.click();
    await check(coder.getByText(label, { exact: true })).toBeVisible();
    await check(disclosure).toBeFocused();
    expect(session.pageErrors).toEqual([]);
  } finally {
    await session.close();
  }
});
