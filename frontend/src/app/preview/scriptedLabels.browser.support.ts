import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Page } from "@playwright/test";
import { expect } from "vitest";
import { chatEvents, previewChatSupport } from "./previewChat.browser.support";
import { captureCheckpoint } from "./previewPanel.browser.support";

export const sizes = [
  { width: 1920, height: 1080 },
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
];
export const matrix = ["coder", "lead"].flatMap((scene) =>
  sizes.flatMap((viewport) => ["dark", "day"].map((theme) => ({ scene, viewport, theme }))),
);
export function scriptedLabelsSupport() {
  const host = previewChatSupport();
  return {
    async session(viewport = sizes[0], theme = "dark", variant: boolean | "mixed" = false) {
      const session = await host.session(viewport, theme);
      if (variant) {
        host.events = chatEvents().map((event) => {
          if (event.type === "run.started") {
            const models =
              variant === "mixed"
                ? [
                    ...event.payload.models,
                    {
                      id: "muse",
                      label: "Muse Spark",
                      provider: "meta" as const,
                      model: "muse",
                      reasoning: null,
                    },
                  ]
                : event.payload.models.map((model) => ({ ...model, provider: "meta" as const }));
            return { ...event, payload: { ...event.payload, models } };
          }
          if (variant === "mixed" && event.type === "lead.status")
            return { ...event, payload: { ...event.payload, model: "muse" } };
          // Old reference fixtures omit this field; mixed membership needs explicit contract null.
          if (variant === "mixed" && event.type === "agent.status")
            return { ...event, payload: { ...event.payload, plan_id: null } };
          return event;
        });
        await session.page.reload();
        await session.page.locator(".chat-lead-row").click();
      }
      return { ...session, append: host.append.bind(host) };
    },
  };
}
export async function selectScene(
  session: Awaited<ReturnType<ReturnType<typeof scriptedLabelsSupport>["session"]>>,
  scene: string,
) {
  const row = session.page.locator(
    scene === "coder" ? '[data-agent-origin="agent-1"]' : ".chat-lead-row",
  );
  await row.click();
  await check(row).toHaveAttribute("aria-pressed", "true");
  return row;
}
async function waitForConversation(page: Page, scene: string) {
  const agentId = scene === "coder" ? "agent-1" : "agent-900001";
  await check(page.locator(".chat-agents")).toHaveAttribute("data-selected-agent", agentId);
  const panel = page.locator(".conversation-panel");
  await check(panel).toHaveAttribute(
    "aria-label",
    scene === "coder" ? "Agent A conversation" : "Lead conversation",
  );
  const thread = panel.locator(".conversation-entries");
  await check(thread).toBeVisible();
  await check(thread.getByRole("listitem").first()).toBeVisible();
  await check(thread).toContainText("Task from you");
  if (scene === "coder")
    await check(
      thread.getByRole("article", { name: "Proposed change", exact: true }),
    ).toBeVisible();
  else
    await check(thread).toContainText(
      "The coupon change is ready to try. Check the shop, then review the change.",
    );
  await check(panel.locator(".conversation-composer")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}
async function captureOverflow(page: Page) {
  return page.evaluate(() => {
    const selectors = [
      "html",
      "body",
      ".chat-agents",
      ".conversation-header",
      ".conversation-panel",
      ".conversation-timeline",
      ".conversation-composer",
      ".scripted-agent-label",
      ".scripted-agent-explanation",
    ];
    return Object.fromEntries(
      selectors.map((selector) => [
        selector,
        Math.max(
          0,
          ...Array.from(
            document.querySelectorAll(selector),
            (node) => node.scrollWidth - node.clientWidth,
          ),
        ),
      ]),
    );
  });
}
export async function capture(
  session: Awaited<ReturnType<ReturnType<typeof scriptedLabelsSupport>["session"]>>,
  scene: string,
) {
  const { page, viewport, theme, pageErrors } = session;
  await waitForConversation(page, scene);
  const horizontalOverflow = await captureOverflow(page);
  const stage = process.env.SCRIPTED_LABELS_CAPTURE ?? "after";
  const directory = fileURLToPath(
    new URL(`../../../../.shots/scripted-labels/${stage}/`, import.meta.url),
  );
  await mkdir(directory, { recursive: true });
  const stem = `${scene}-${String(viewport.width)}-${theme}`;
  const checkpoint = await captureCheckpoint();
  await page.screenshot({ path: `${directory}${stem}.png` });
  await writeFile(
    `${directory}${stem}.json`,
    JSON.stringify(
      { scene, viewport, theme, stage, ...checkpoint, pageErrors, horizontalOverflow },
      null,
      2,
    ) + "\n",
  );
  expect(pageErrors).toEqual([]);
  expect(Object.values(horizontalOverflow)).toEqual(Object.values(horizontalOverflow).map(() => 0));
}
