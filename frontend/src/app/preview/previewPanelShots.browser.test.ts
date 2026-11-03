import { afterAll, expect, it } from "vitest";
import { readFile } from "node:fs/promises";
import { expect as check } from "@playwright/test";
import {
  previewBrowserSupport,
  scenarios,
  sizes,
  shotsDirectory,
  captureCheckpoint,
} from "./previewPanel.browser.support";
import type { FrameManifest } from "./previewPanel.browser.support";

const support = previewBrowserSupport("shots");
const frames: FrameManifest[] = [];
const cases = sizes.flatMap((viewport) => ["dark", "day"].map((theme) => ({ viewport, theme })));
afterAll(async () => {
  expect(frames).toHaveLength(150);
  await support.manifest(frames);
});
it.each(cases)(
  "captures 22 states and 3 references at $viewport.width in $theme",
  async ({ viewport, theme }) => {
    const checkpoint = await captureCheckpoint();
    expect(scenarios).toHaveLength(22);
    let review: FrameManifest | undefined;
    for (const mode of ["review", "usage", "settings"]) {
      const session = await support.session({ ...viewport, theme });
      try {
        const page = await session.open(mode);
        if (viewport.width === 390 && mode !== "review")
          await check(page.locator(".narrow-notice")).toBeVisible();
        const reference = await support.capture(page, `reference-${mode}`, viewport, theme);
        frames.push(reference);
        if (mode === "review") review = reference;
      } finally {
        await session.close();
      }
    }
    if (!review) throw new Error("Missing built review reference");
    for (const scenario of scenarios) {
      const session = await support.session({ ...viewport, theme, scenario });
      try {
        const page = await session.open();
        await check(page.getByRole("button", { name: "Close", exact: true })).toBeVisible();
        if (scenario === "ready") await check(page.locator("iframe")).toBeVisible();
        if (scenario === "failed-stop" || scenario === "stopping-closed")
          await check(page.locator("iframe")).toHaveCount(0);
        if (scenario === "stopping-closed") {
          await check(page.locator(".preview-panel-state")).toHaveText("Stopping");
          await check(page.getByRole("status")).toContainText("Waiting for verified closure.");
        }
        if (scenario === "failed-stop") {
          await check(page.locator(".preview-panel-state")).toHaveText("Unconfirmed");
          await check(page.getByRole("alert")).toContainText("Closure is unconfirmed");
          await check(page.getByText("Waiting for verified closure.")).toHaveCount(0);
        }
        const frame = await support.capture(page, scenario, viewport, theme);
        const manifest: unknown = JSON.parse(
          await readFile(`${shotsDirectory}${frame.path.replace(/\.png$/, ".json")}`, "utf8"),
        );
        expect(manifest).toMatchObject({
          ...checkpoint,
          png: frame.path,
        });
        expect(checkpoint.checkpoint).toMatch(/^[a-f0-9]{40}$/);
        expect(typeof checkpoint.workingTree).toBe("boolean");
        frames.push(frame);
        await support.compare(frame, review);
      } finally {
        await session.close();
      }
    }
  },
  90_000,
);
