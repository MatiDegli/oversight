import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { ChangeFacts } from "@contracts";
import { chromium } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

let server: ViteDevServer;
let browser: Browser;
let url: string;
let html: string;
const shots = fileURLToPath(new URL("../../../.shots/measured-cone-review/", import.meta.url));

declare global {
  interface Window {
    measuredFacts: ChangeFacts[];
  }
}

beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}test-cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { reviewTest } from "/src/app/review.browser.fixture.tsx"; import { measuredFacts } from "/src/review/measuredCallers.fixture.ts"; window.reviewTest = reviewTest; window.measuredFacts = measuredFacts;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
  if (process.env.MEASURED_SHOTS === "1") await mkdir(shots, { recursive: true });
}, 60_000);

afterAll(async () => {
  await browser.close();
  await server.close();
});

async function mount(page: Page, theme: string) {
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => "measuredFacts" in window);
  await page.evaluate((theme) => {
    document.documentElement.dataset.theme = theme;
    window.reviewTest.mount(false, 20);
    const provenance = document.createElement("p");
    provenance.textContent = "Hand-written UI fixture · illustrative measurements";
    provenance.style.cssText =
      "position:fixed;bottom:4px;left:300px;color:var(--muted);font-size:var(--caption-size);z-index:100";
    document.body.append(provenance);
  }, theme);
  await page.getByRole("button", { name: /^Decisions:/ }).click();
  await page.locator(".monaco-diff-editor").first().waitFor();
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".monaco-diff-editor .view-lines")].some((lines) =>
      lines.textContent.includes("percent"),
    ),
  );
  await page.evaluate(() => document.fonts.ready);
}

async function publish(page: Page, index: number, summary: boolean, stress = false) {
  await page.evaluate(
    ({ index, summary, stress }) => {
      const facts = window.measuredFacts[index];
      if (!facts) throw new Error("Missing fixture");
      const state = window.reviewTest.state();
      const proposal = state.changes["change-1"]?.proposal;
      if (!proposal) throw new Error("Missing proposal");
      if (stress)
        window.reviewTest.emit({
          seq: 0,
          ts: "2026-10-29T15:00:00.000Z",
          type: "change.proposed",
          payload: {
            ...proposal,
            impact: {
              ...proposal.impact,
              omitted_by_depth: 12,
              omitted_by_limit: 3,
              entries: [
                { node_id: "unknown_caller_".repeat(24), distance: 1 },
                ...proposal.impact.entries,
              ],
            },
          },
        });
      window.reviewTest.emit({
        seq: 0,
        ts: "2026-10-29T15:00:00.000Z",
        type: "change.facts_recorded",
        payload: {
          ...facts,
          summary: summary ? facts.summary : null,
          measured_cone: stress
            ? (facts.measured_cone?.map((caller) => ({
                ...caller,
                problems: ["Published diagnostic ".repeat(40), ...caller.problems],
              })) ?? null)
            : facts.measured_cone,
        },
      });
    },
    { index, summary, stress },
  );
}

async function checkGeometry(page: Page) {
  const panel = page.getByRole("complementary", { name: "Review panel" });
  const geometry = await panel.evaluate((element) => {
    const get = (selector: string) => {
      const found = element.querySelector<HTMLElement>(selector);
      if (!found) throw new Error(selector);
      return found;
    };
    const context = get('[aria-label="Review context"]');
    const decision = get('[aria-label="Decision"]');
    const diff = get('[aria-label^="Diff of"] > div');
    const measured = element.querySelector<HTMLElement>(
      '[aria-label="Measured callers · Possibly affected"]',
    );
    return {
      decisionBottom: decision.getBoundingClientRect().bottom,
      panelBottom: element.getBoundingClientRect().bottom,
      diffHeight: diff.getBoundingClientRect().height,
      contextHeight: context.clientHeight,
      overflow: context.scrollWidth > context.clientWidth,
      nested: [...context.querySelectorAll("*")].some(
        (child) =>
          !child.closest(".monaco-diff-editor") &&
          /auto|scroll/.test(getComputedStyle(child).overflowY),
      ),
      measuredOverflow: measured ? measured.scrollWidth > measured.clientWidth : false,
    };
  });
  expect(geometry.decisionBottom).toBeLessThanOrEqual(geometry.panelBottom);
  expect(geometry.diffHeight).toBeGreaterThanOrEqual(192);
  expect(geometry.contextHeight).toBeGreaterThan(0);
  expect(geometry.overflow || geometry.measuredOverflow || geometry.nested).toBe(false);
  expect(await page.getByRole("main", { name: "Graph stage" }).isVisible()).toBe(true);
  expect(
    await page.getByRole("region", { name: "Possibly affected", exact: true }).isVisible(),
  ).toBe(true);
}

const cases = ["available", "partial", "unavailable", "absent"];
for (const width of [1920, 1280])
  for (const theme of ["dark", "day"])
    for (const summary of [true, false]) {
      it.each(cases)(
        `keeps %s evidence, diff, decisions and focus at ${String(width)} in ${theme}, summary ${String(summary)}`,
        async (label) => {
          const page = await browser.newPage({
            viewport: { width, height: width === 1920 ? 1080 : 800 },
            deviceScaleFactor: 1,
            reducedMotion: "reduce",
          });
          const errors: string[] = [];
          page.on("pageerror", (error) => errors.push(error.message));
          try {
            await mount(page, theme);
            const comment = page.getByRole("textbox", { name: "Comment for the agent" });
            await comment.fill("Keep my review comment");
            await publish(page, cases.indexOf(label), summary);
            const measured = page.getByRole("region", {
              name: "Measured callers · Possibly affected",
              exact: true,
            });
            expect(await comment.evaluate((element) => document.activeElement === element)).toBe(
              true,
            );
            expect(await comment.inputValue()).toBe("Keep my review comment");
            const hasDetails = label === "available" || label === "partial";
            if (hasDetails) await page.getByText("Details", { exact: true }).click();
            else {
              expect(
                await page
                  .getByText(
                    label === "absent"
                      ? "Nothing was compared for this change."
                      : "Nothing was compared.",
                    { exact: true },
                  )
                  .count(),
              ).toBe(1);
              expect(await page.getByText("Details", { exact: true }).count()).toBe(0);
              expect(await measured.count()).toBe(0);
            }
            if (hasDetails)
              expect(await measured.textContent()).toContain(
                label === "available" ? "Comparisons available" : "Partial measurements",
              );
            if (label !== "absent") await checkGeometry(page);
            if (hasDetails)
              await measured.evaluate((element) => {
                const context = element.closest('[aria-label="Review context"]');
                if (context)
                  context.scrollTop +=
                    element.getBoundingClientRect().top - context.getBoundingClientRect().top;
              });
            if (process.env.MEASURED_SHOTS === "1")
              await page.screenshot({
                path: `${shots}review-${theme}-${label}-${String(width)}-${summary ? "summary" : "no-summary"}.png`,
              });
            await comment.focus();
            await publish(page, 1, summary, true);
            expect(await comment.evaluate((element) => document.activeElement === element)).toBe(
              true,
            );
            const details = page.locator(".review-details > details");
            if ((await details.getAttribute("open")) === null)
              await details.locator(":scope > summary").click();
            await checkGeometry(page);
            const panel = page.getByRole("complementary", { name: "Review panel" });
            const header = panel.locator(".review-header");
            expect(await header.getByRole("listitem").count()).toBe(4);
            expect(await header.locator('[aria-label="Impact cone"]').textContent()).toBe(
              "10 possibly affected · up to distance 2",
            );
            expect(await header.locator('[aria-label="Possible callers"]').count()).toBe(0);
            expect(await header.locator(".lucide-circle-help").count()).toBe(0);
            if (label !== "absent")
              expect(await measured.textContent()).toContain(
                "Static cone bounds: depth 3 · 40 nodes",
              );
            expect(errors).toEqual([]);
          } finally {
            await page.close();
          }
        },
        60_000,
      );
    }
