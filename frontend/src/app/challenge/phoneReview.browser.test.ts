import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Browser, Page } from "@playwright/test";
import { expect as check } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { recording } from "./recording.fixture";
import { launchBrowser } from "../../browserLaunch.support";

let server: ViteDevServer, browser: Browser, url: string;
const before = process.env.PHONE_REVIEW_BEFORE === "1";
const shots = fileURLToPath(new URL("../../../../.shots/phone-review/", import.meta.url));
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    mode: "replay",
    cacheDir: `${shots}cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
  await mkdir(shots, { recursive: true });
}, 60000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function capture(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  return page.screenshot({ path: `${shots}${name}.png` });
}
it.each(["dark", "day"])(
  "captures recorded decisions in %s",
  async (theme) => {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    try {
      await page.goto(url);
      await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
      await page.getByRole("button", { name: "Start practice", exact: true }).click();
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      for (const index of [1, 2]) {
        await check(page.locator(".pp-progress")).toContainText(`Decision ${String(index)} of 2`);
        await capture(page, `${before ? "before" : "after"}-${String(index)}-${theme}`);
        if (!before) {
          await check(page.getByRole("region", { name: "Review lead" })).toHaveText(
            "Nothing was compared for this change.Behavior and callers were not recorded for this revision. The diff and tests are the evidence.",
          );
          expect(
            await page
              .locator(".review-body > *")
              .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label"))),
          ).toEqual(["Review lead", "Agent note", null]);
          await check(page.locator('[aria-label="Facts"] li')).toHaveText(
            index === 1
              ? ["2 files", "+9 −0", "29 passed", "No exact callers"]
              : ["2 files", "+28 −0", "32 passed", "9 possibly affected"],
          );
          await check(page.locator(".pp-decision-row")).toHaveCount(1);
          await check(page.locator(".pp-decision-row summary")).toContainText("Recorded task");
          await check(page.getByText("What was compared", { exact: true })).toHaveCount(0);
          await check(
            page.locator(".pp-decision-row").filter({ hasText: "Possibly affected" }),
          ).toHaveCount(0);
          const toggle = page.getByRole("button", { name: /^Show diff/ });
          await check(toggle).toContainText(
            index === 1
              ? "shop/inventory.py and 1 more file · +9 −0"
              : "shop/pricing.py and 1 more file · +28 −0",
          );
          await toggle.click();
          await check(page.getByRole("button", { name: /^Hide diff/ })).toHaveAttribute(
            "aria-expanded",
            "true",
          );
          await check(page.locator(".pp-code").first()).toBeVisible();
          if (index === 2 && theme === "dark") await capture(page, "after-2-diff-dark");
          await page.getByRole("button", { name: /^Hide diff/ }).click();
          await check(toggle).toHaveAttribute("aria-expanded", "false");
          await check(page.locator(".pp-code")).toHaveCount(0);
        }
        if (index === 1) {
          await page.getByRole("button", { name: "Reject", exact: true }).click();
          await page.getByRole("button", { name: "Next", exact: true }).click();
        }
      }
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60000,
);

it.skipIf(before).each(["a", "b"] as const)(
  "reviews fixture %s with the shared phone evidence",
  async (name) => {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const html = await server.transformIndexHtml(
      `/phone-${name}.html`,
      `<!doctype html><html><body><script type="module">
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import '/src/theme/index.css'; import '/src/app/challenge/phone.css'; import '/src/app/challenge/phoneGraph.css';
    import { PhoneReview } from '/src/app/challenge/PhoneReview.tsx';
    import { DecisionDock } from '/src/app/challenge/PhoneDecision.tsx';
    import { PhoneGraph } from '/src/app/challenge/PhoneGraph.tsx';
    import { headerState, headerCases } from '/src/review/ReviewHeader.fixture.tsx';
    import { manifest, validateManifest } from '/src/app/challenge/manifest.ts';
    const recording = ${JSON.stringify(recording)};
    import { freshAttempt } from '/src/app/challenge/PracticePanel.tsx';
    const state = headerState('${name}'), changeId = headerCases['${name}'].changeId;
    const proposal = state.changes[changeId].proposal;
    const base = validateManifest(manifest, recording);
    const entry = { ...base.cases[${name === "a" ? "0" : "1"}], changeId, proposal };
    const practice = { ...base, cut: state, cases: [entry] };
    const h = React.createElement, host = document.createElement('div'); document.body.append(host);
    createRoot(host).render(h('div', { className: 'phone-practice' },
      h('div', { className: 'pp-topbar' }, 'Oversight'),
      h('div', { className: 'pp-status' }, 'Hand-written fixture'),
      h(PhoneGraph, { practice, entry, merged: false }),
      h('main', { className: 'pp-main' }, h(PhoneReview, { state, changeId, title: entry.title, slot: state.agents[proposal.agent_id].slot })),
      h(DecisionDock, { practice, attempt: freshAttempt() })
    ));
  </script></body></html>`,
    );
    try {
      await page.route(`**/phone-${name}.html`, (route) =>
        route.fulfill({ contentType: "text/html", body: html }),
      );
      await page.goto(new URL(`phone-${name}.html`, url).href);
      await page.waitForFunction(() => document.querySelector(".review-lead"));
      expect(errors).toEqual([]);
      const lead = page.getByRole("region", { name: "Review lead" });
      await check(lead).toContainText(
        name === "a" ? "2 choices need you" : "1 caller behaves differently",
      );
      const approve = page.getByRole("button", { name: "Approve", exact: true });
      for (const theme of ["dark", "day"]) {
        await page.evaluate((theme) => {
          document.documentElement.dataset.theme = theme;
        }, theme);
        const product = await capture(page, `${name}-${theme}`);
        if (theme === "dark") {
          const sketch = await readFile(
            new URL(
              `../../../../docs/sketches/review/${name === "a" ? "f" : "f-b"}-dark.png`,
              import.meta.url,
            ),
          );
          const report = await browser.newPage({ viewport: { width: 780, height: 844 } });
          await report.setContent(
            `<style>body{margin:0;display:flex}img{width:390px;height:844px;object-fit:contain;object-position:top}</style><img src="data:image/png;base64,${product.toString("base64")}"><img src="data:image/png;base64,${sketch.toString("base64")}">`,
          );
          await report.screenshot({ path: `${shots}${name}-compare.png` });
          await report.close();
        }
      }
      const example = page.locator(".example-pair").first();
      const boxes = await example.locator(":scope > div").evaluateAll((els) =>
        els.map((el) => ({
          y: el.getBoundingClientRect().y,
          bottom: el.getBoundingClientRect().bottom,
        })),
      );
      expect(boxes[1]?.y).toBeGreaterThan(boxes[0]?.bottom ?? 0);
      const row = page.locator(".review-compared > details > summary");
      await check(row).toHaveText(
        `What was compared${name === "a" ? "24 inputs, 2 differ · 1 of 36 functions" : "10 inputs, 9 differ · 6 callers not measured"}`,
      );
      expect((await row.boundingBox())?.height).toBe(44);
      await row.click();
      await check(page.locator(".comparison-lines")).toBeVisible();
      if (name === "a") {
        await check(lead).toContainText("1 of 2 answered");
        expect(await page.getByText("1 of 2 answered", { exact: true }).count()).toBe(1);
        await check(approve).toBeDisabled();
        await check(page.locator(".pp-lock-caption")).toHaveText(
          "Approve opens when every answer is Yes.",
        );
        const answers = page.locator(".decision-actions button");
        for (const answer of await answers.all()) {
          const box = await answer.boundingBox();
          expect(box?.height).toBeGreaterThanOrEqual(44);
          expect(box?.width).toBe(
            (await page.locator(".panel-choice-actions").boundingBox())?.width,
          );
        }
        await page.getByRole("button", { name: "Yes, keep it", exact: true }).click();
      } else
        await check(page.getByLabel("Impact cone", { exact: true })).toHaveText(
          "9 possibly affected",
        );
      await check(approve).toBeEnabled();
      await check(page.locator(".pp-lock-caption")).toHaveCount(0);
      expect(
        await page.locator(".pp-main").evaluate((el) => el.scrollWidth <= el.clientWidth),
      ).toBe(true);
    } finally {
      await page.close();
    }
  },
  60000,
);
