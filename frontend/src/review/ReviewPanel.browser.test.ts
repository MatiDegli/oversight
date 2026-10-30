import { launchBrowser } from "../browserLaunch.support";
import { fileURLToPath } from "node:url";

import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;

beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/summary/test-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { reviewTest } from "/src/app/review.browser.fixture.tsx"; window.reviewTest = reviewTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);

afterAll(async () => {
  await browser?.close();
  await server?.close();
});

it.each([
  { width: 1280, height: 800, recorded: true },
  { width: 1920, height: 1080, recorded: true },
  { width: 1280, height: 800, recorded: false },
  { width: 1920, height: 1080, recorded: false },
])(
  "keeps controls and the diff outside bounded context at $width × $height (recorded: $recorded)",
  async ({ width, height, recorded }) => {
    if (!browser) throw new Error("Browser was not started");
    const page = await browser.newPage({ viewport: { width, height } });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "reviewTest" in window);
      await page.evaluate((recorded) => {
        window.reviewTest.mount(recorded, 20);
        const proposal = window.reviewTest.state().changes["change-1"]?.proposal;
        if (!proposal) throw new Error("Missing proposal");
        window.reviewTest.emit({
          seq: 0,
          ts: "2026-10-28T12:00:00Z",
          type: "change.facts_recorded",
          payload: {
            change_id: proposal.change_id,
            revision: proposal.revision,
            base_commit: proposal.base_commit,
            head_commit: proposal.head_commit,
            main_commit: proposal.base_commit,
            candidate_tree: "tree",
            declared: null,
            edited_paths: ["shop/pricing.py"],
            test_runs: [],
            integrity: [],
            added_tests: [],
            run_findings: [],
            zone: null,
            reach: null,
            complete: true,
            problems: [],
            summary: ["Task: Cap the discount", "Changed: 1 file", "Other results: cost unknown"],
            behavior_diff: null,
            measured_cone: null,
            review_cards: null,
            shape: null,
            shape_complete: null,
          },
        });
      }, recorded);
      await page.getByRole("button", { name: /^Decisions:/ }).click();
      const panel = page.getByRole("complementary", { name: "Review panel" });
      await panel.locator(".line-insert").first().waitFor();
      const geometry = () =>
        panel.evaluate((panel) => {
          const required = (selector: string) => {
            const element = panel.querySelector<HTMLElement>(selector);
            if (!element) throw new Error(`Missing ${selector}`);
            return element;
          };
          const context = required('[aria-label="Review context"]');
          const close = required('[aria-label="Close review"]');
          const decision = required('[aria-label="Decision"]');
          const diff = required('[aria-label^="Diff of"]');
          const editor = required(".monaco-diff-editor");
          const bounds = (element: Element) => {
            const { top, bottom, height } = element.getBoundingClientRect();
            return { top, bottom, height };
          };
          return {
            panel: bounds(panel),
            header: bounds(required("header")),
            context: bounds(context),
            decision: bounds(decision),
            diff: bounds(diff),
            editor: bounds(editor),
            diffViewport: bounds(required('[aria-label^="Diff of"] > div')),
            close: bounds(close),
            lineHeight: Number.parseFloat(
              getComputedStyle(required('[aria-label="Recorded facts summary"] p')).lineHeight,
            ),
            panelScrolls: /auto|scroll/.test(getComputedStyle(panel).overflowY),
            outsideContext: [close, decision, editor].every((el) => !context.contains(el)),
            contextScrolls: getComputedStyle(context).overflowY === "auto",
            nestedScrolls: [...context.querySelectorAll("*")].some((element) =>
              /auto|scroll/.test(getComputedStyle(element).overflowY),
            ),
          };
        });
      const before = await geometry();
      expect(before.panelScrolls).toBe(false);
      expect(before.outsideContext).toBe(true);
      expect(before.contextScrolls).toBe(true);
      expect(before.nestedScrolls).toBe(false);
      expect(before.close.top).toBeGreaterThanOrEqual(before.panel.top);
      expect(before.decision.bottom).toBeLessThanOrEqual(before.panel.bottom);
      expect(before.diffViewport.height).toBeGreaterThanOrEqual(192);
      expect(before.editor.height).toBeGreaterThan(0);
      expect(before.context.height).toBeGreaterThanOrEqual(before.lineHeight);
      expect(before.diff.height).toBeGreaterThanOrEqual(232);
      expect(before.header.bottom).toBeLessThanOrEqual(before.context.top);
      expect(before.context.bottom).toBeLessThanOrEqual(before.diff.top);
      expect(before.diff.bottom).toBeLessThanOrEqual(before.decision.top);
      expect(before.editor.bottom).toBeLessThanOrEqual(before.diff.bottom);

      // Maximum-length server lines, extensive signals and a long agent note share one scroll.
      await page.evaluate(() => {
        const state = window.reviewTest.state();
        const proposal = state.changes["change-1"]?.proposal;
        const facts = state.changeFacts["change-1"]?.at(-1);
        if (!proposal || !facts) throw new Error("Missing facts");
        window.reviewTest.emit({
          seq: 0,
          ts: "2026-10-28T12:00:01Z",
          type: "change.proposed",
          payload: { ...proposal, summary: "Agent note. ".repeat(200) },
        });
        window.reviewTest.emit({
          seq: 0,
          ts: "2026-10-28T12:00:02Z",
          type: "change.facts_recorded",
          payload: {
            ...facts,
            summary: ["x".repeat(240), "y".repeat(240), "z".repeat(240)],
            complete: false,
            problems: Array.from({ length: 50 }, () => "Evidence unavailable"),
          },
        });
      });
      const context = panel.getByRole("region", { name: "Review context", exact: true });
      const scrolled = await context.evaluate((element) => {
        const lines = element.querySelectorAll('[aria-label="Recorded facts summary"] p');
        const readable = [...lines].every((line) => {
          const style = getComputedStyle(line);
          return (
            line.scrollWidth <= line.clientWidth &&
            line.scrollHeight <= line.clientHeight &&
            style.overflowY === "visible" &&
            style.webkitLineClamp === "none"
          );
        });
        element.scrollTop = element.scrollHeight;
        return { readable, scrollTop: element.scrollTop, height: element.clientHeight };
      });
      expect(scrolled.readable).toBe(true);
      expect(scrolled.height).toBeGreaterThan(0);
      expect(scrolled.scrollTop).toBeGreaterThan(0);
      const after = await geometry();
      expect(after.close).toEqual(before.close);
      expect(after.decision).toEqual(before.decision);
      expect(after.diffViewport.height).toBeGreaterThanOrEqual(192);
      expect(after.editor.height).toBeGreaterThan(0);
      expect(after.diff.bottom).toBeLessThanOrEqual(after.decision.top);
      expect(after.editor.bottom).toBeLessThanOrEqual(after.diff.bottom);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60_000,
);
