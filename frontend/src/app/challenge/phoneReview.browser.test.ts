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
async function revealDecisions(page: Page) {
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await page.getByRole("button", { name: "Show findings", exact: true }).click();
}

it.each(["dark", "day"])("preserves agent and node sizes in %s", async (theme) => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  try {
    await page.goto(url);
    await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
    await page.getByRole("button", { name: "Start practice", exact: true }).click();
    await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
    }, theme);
    for (const view of ["decision", "reveal"]) {
      const agents = page.locator(".pp-agent");
      await check(agents).toHaveCount(view === "decision" ? 1 : 2);
      for (const agent of await agents.all()) {
        await check(agent).toHaveCSS("font-size", "13px");
        await check(agent.locator(".pp-letter")).toHaveCSS("width", "22px");
        await check(agent.locator(".pp-letter")).toHaveCSS("height", "22px");
      }
      const badges = page.locator(".pp-node .pp-letter");
      await check(badges).toHaveCount(view === "decision" ? 1 : 2);
      for (const badge of await badges.all()) {
        await check(badge).toHaveCSS("width", view === "decision" ? "22px" : "20px");
        await check(badge).toHaveCSS("height", view === "decision" ? "22px" : "20px");
      }
      if (view === "decision") await revealDecisions(page);
    }
  } finally {
    await page.close();
  }
});

it("preserves dock wrapping without a lock caption across practice screens", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  try {
    await page.goto(url);
    await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
    await check(page.locator(".pp-dock")).toHaveCSS("flex-wrap", "nowrap");
    await page.getByRole("button", { name: "Start practice", exact: true }).click();
    await check(page.locator(".pp-dock")).toHaveCSS("flex-wrap", "nowrap");
    await page.getByRole("button", { name: "Reject", exact: true }).click();
    await check(page.locator(".pp-dock")).toHaveCSS("flex-wrap", "nowrap");
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("button", { name: "Reject", exact: true }).click();
    await page.getByRole("button", { name: "Show findings", exact: true }).click();
    await check(page.locator(".pp-dock")).toHaveCSS("flex-wrap", "nowrap");
    await page.getByRole("button", { name: "See your result", exact: true }).click();
    await check(page.locator(".pp-dock")).toHaveCSS("flex-wrap", "nowrap");
  } finally {
    await page.close();
  }
});

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

type FixtureName = "a" | "b" | "c" | "d2";
async function fixturePage(name: FixtureName) {
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

    return { page, errors };
  } catch (error) {
    await page.close();
    throw error;
  }
}

async function assertLeadRow(page: Page) {
  const geometry = await page.locator(".review-lead-heading").evaluate((el) => {
    const heading = el.querySelector("h3"),
      caption = el.querySelector("p");
    if (!heading || !caption) throw new Error("Missing lead row");
    const h = heading.getBoundingClientRect(),
      c = caption.getBoundingClientRect();
    return {
      top: c.top,
      headingTop: h.top,
      headingBottom: h.bottom,
      right: c.right,
      leadRight: el.getBoundingClientRect().right,
      height: c.height,
      lineHeight: Number.parseFloat(getComputedStyle(caption).lineHeight),
    };
  });
  expect(geometry.top).toBeGreaterThanOrEqual(geometry.headingTop);
  expect(geometry.top).toBeLessThan(geometry.headingBottom);
  expect(Math.abs(geometry.right - geometry.leadRight)).toBeLessThanOrEqual(1);
  expect(geometry.height).toBe(geometry.lineHeight);
}

async function captureFixture(page: Page, name: FixtureName) {
  for (const theme of ["dark", "day"]) {
    await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
    }, theme);
    await page.evaluate(() => document.fonts.ready);
    if (name !== "d2") await assertLeadRow(page);
    const product = await capture(page, `${name}-${theme}`);
    if (theme !== "dark" || (name !== "a" && name !== "b")) continue;
    const sketch = await readFile(
      new URL(
        `../../../../docs/sketches/review/${name === "a" ? "f" : "f-b"}-dark.png`,
        import.meta.url,
      ),
    );
    const report = await browser.newPage({ viewport: { width: 780, height: 844 } });
    try {
      await report.setContent(
        `<style>body{margin:0;display:flex}img{width:390px;height:844px;object-fit:contain;object-position:top}</style><img src="data:image/png;base64,${product.toString("base64")}"><img src="data:image/png;base64,${sketch.toString("base64")}">`,
      );
      await report
        .locator("img")
        .evaluateAll((images) =>
          Promise.all(images.map((img) => (img as HTMLImageElement).decode())),
        );
      await report.screenshot({ path: `${shots}${name}-compare.png` });
    } finally {
      await report.close();
    }
  }
}

async function assertExamplesAndComparison(page: Page, name: "a" | "b") {
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
}

async function assertA(page: Page) {
  const lead = page.getByRole("region", { name: "Review lead" });
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await check(lead).toContainText("1 of 2 answered");
  expect(await page.getByText("1 of 2 answered", { exact: true }).count()).toBe(1);
  await check(approve).toBeDisabled();
  await check(page.locator(".pp-lock-caption")).toHaveText(
    "Approve opens when every answer is Yes.",
  );
  await check(page.locator(".pp-dock")).toHaveCSS("flex-wrap", "wrap");
  const answers = page.locator(".decision-actions button");
  for (const answer of await answers.all()) {
    const box = await answer.boundingBox();
    expect(box?.height).toBeGreaterThanOrEqual(44);
    expect(box?.width).toBe((await page.locator(".panel-choice-actions").boundingBox())?.width);
  }
  await page.getByRole("button", { name: "Yes, keep it", exact: true }).click();
  await assertApproval(page);
  await check(page.locator(".pp-taxi path")).toHaveCount(0);
}

async function assertB(page: Page) {
  await check(page.locator(".review-lead-heading > p")).toHaveText("3 of 9 measured");
  await check(page.getByLabel("Impact cone", { exact: true })).toHaveText("9 possibly affected");
  const cells = page.locator(".example-pair > div");
  for (const cell of await cells.all()) {
    const geometry = await cell.evaluate((el) => {
      const label = el.querySelector("span"),
        value = el.querySelector("code");
      if (!label || !value) throw new Error("Missing example");
      const css = getComputedStyle(el);
      return {
        labelY: label.getBoundingClientRect().top,
        valueY: value.getBoundingClientRect().top,
        padding: [css.paddingTop, css.paddingRight, css.paddingBottom, css.paddingLeft],
      };
    });
    expect(Math.abs(geometry.labelY - geometry.valueY)).toBeLessThanOrEqual(1);
    expect(geometry.padding).toEqual(["9px", "12px", "9px", "12px"]);
  }
  await assertApproval(page);
}

async function assertC(page: Page) {
  await check(page.getByRole("region", { name: "Review lead" })).toContainText(
    "Read the diff before approving",
  );
  await assertApproval(page);
}

async function assertD2(page: Page) {
  await check(page.getByRole("region", { name: "Review lead" })).toHaveText(
    "Nothing was compared: it uses randomness; it reads the clock.The diff and tests are the evidence.",
  );
  await assertApproval(page);
}

async function assertApproval(page: Page) {
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await check(approve).toBeEnabled();
  await check(page.locator(".pp-lock-caption")).toHaveCount(0);
  await check(page.locator(".pp-dock")).toHaveCSS("flex-wrap", "nowrap");
  expect(await page.locator(".pp-main").evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true,
  );
}

// Same header states as the panel; c and d2 have no approved phone sketch.
it.skipIf(before).each(["a", "b", "c", "d2"] as const)(
  "reviews fixture %s with the shared phone evidence",
  async (name) => {
    const { page, errors } = await fixturePage(name);
    try {
      const lead = page.getByRole("region", { name: "Review lead" });
      await check(lead).toContainText(
        {
          a: "2 choices need you",
          b: "1 caller behaves differently",
          c: "Partial evidence",
          d2: "Nothing was compared: it uses randomness; it reads the clock.",
        }[name],
      );
      await captureFixture(page, name);
      if (name === "a" || name === "b") await assertExamplesAndComparison(page, name);
      await { a: assertA, b: assertB, c: assertC, d2: assertD2 }[name](page);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60_000,
);
