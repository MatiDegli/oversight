import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import { launchBrowser } from "../browserLaunch.support";
import type { GraphProbeHost } from "../graph";

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
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { mountHeader, headerState } from "/src/review/ReviewHeader.fixture.tsx"; window.mountHeader = mountHeader; window.headerState = headerState; import {reviewTest} from "/src/app/review.browser.fixture.tsx"; window.reviewTest = reviewTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);

afterAll(async () => {
  await browser?.close();
  await server?.close();
});

const shots = fileURLToPath(new URL("../../../.shots/review-lead/", import.meta.url));
it.each(["b", "c"] as const)(
  "opens %s Details at 1920 without overflow or moving focus",
  async (name) => {
    if (!browser) throw new Error("Missing browser");
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "mountHeader" in window);
      await page.evaluate((name) => {
        document.documentElement.dataset.theme = "dark";
        const api = window as unknown as Pick<
          typeof import("./ReviewHeader.fixture"),
          "mountHeader"
        >;
        api.mountHeader(name);
      }, name);
      const panel = page.getByRole("complementary", { name: "Review panel" });
      await panel.locator(".line-insert").first().waitFor();
      await page.evaluate(() => document.fonts.ready);
      const details = panel.getByRole("region", { name: "Details", exact: true });
      const summary = details.locator(":scope > details > summary");
      expect(await details.locator("details[open]").count()).toBe(0);
      await summary.focus();
      await summary.press("Enter");
      expect(await summary.evaluate((element) => document.activeElement === element)).toBe(true);
      await details.evaluate((element) => {
        for (const samples of element.querySelectorAll("details")) samples.open = true;
      });
      expect(await summary.evaluate((element) => document.activeElement === element)).toBe(true);
      const geometry = await panel.evaluate((element) => ({
        width: element.getBoundingClientRect().width,
        overflow: element.scrollWidth > element.clientWidth,
        detailsOverflow: [
          ...element.querySelectorAll<HTMLElement>(".review-details, .review-details *"),
        ].some((child) => child.clientWidth > 0 && child.scrollWidth > child.clientWidth),
      }));
      expect(geometry).toEqual({ width: 880, overflow: false, detailsOverflow: false });
      if (process.env.REVIEW_DETAILS_SHOTS === "1") {
        const directory = fileURLToPath(
          new URL("../../../.shots/review-details/", import.meta.url),
        );
        await mkdir(directory, { recursive: true });
        await page.waitForFunction(
          () =>
            (document
              .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph?.nodeCenters().length ?? 0) > 0,
        );
        await details.evaluate((element) => {
          const context = element.closest('[aria-label="Review context"]');
          if (context)
            context.scrollTop +=
              element.getBoundingClientRect().top - context.getBoundingClientRect().top;
        });
        await page.screenshot({ path: `${directory}${name}-dark-1920.png` });
        await writeFile(`${directory}${name}-geometry.json`, JSON.stringify(geometry, null, 2));
      }
    } finally {
      await page.close();
    }
  },
);
const expected = {
  padding: 24,
  sectionGap: 24,
  leadGap: 24,
  titleSize: 16,
  titleWeight: 700,
  titleLine: 22,
  captionSize: 13,
  captionLine: 18,
  captionGap: 14,
  noteLabel: 13,
  noteSize: 15,
  noteLine: 22,
  noteGap: 4,
  summarySize: 15,
  summaryLine: 22,
  summaryTitle: 14,
  summaryWeight: 600,
  summaryGap: 4,
  boundGap: 4,
  detailsGap: 8,
  detailsHeight: 44,
  fileHeight: 40,
  pathSize: 14,
  countsSize: 14,
  countsWeight: 600,
  pairGap: 8,
  pairRadius: 10,
  pairPadding: 14,
  pairSize: 15,
  pairLine: 21,
  codeSize: 15,
  codeLine: 21,
  inputSize: 14,
  inputLine: 20,
  pairLabel: 13,
  loneSize: 15,
  loneLine: 22,
  loneWeight: 400,
  iconSize: 16,
  iconGap: 10,
  partialGap: 12,
  partialTop: 14,
  cardBorder: 1,
  cardRadius: 10,
  filePadding: 14,
  chevron: 16,
  fileGap: 8,
  promptSize: 14,
  promptWeight: 600,
  promptGap: 12,
  captionInputGap: 10,
};
async function measure(page: Page) {
  return page.locator(".review-panel").evaluate((panel) => {
    const el = (selector: string) => panel.querySelector<HTMLElement>(selector);
    const css = (selector: string, prop: string) => {
      const e = el(selector);
      return e ? parseFloat(getComputedStyle(e).getPropertyValue(prop)) : null;
    };
    const gap = (a: string, b: string) => {
      const x = el(a),
        y = el(b);
      return x && y ? y.getBoundingClientRect().top - x.getBoundingClientRect().bottom : null;
    };
    return {
      padding: css(".review-body", "padding-left"),
      sectionGap: css(".review-agent-note", "margin-top"),
      leadGap: gap("header", ".review-lead"),
      titleSize: css(".review-lead-heading h3", "font-size"),
      titleWeight: css(".review-lead-heading h3", "font-weight"),
      titleLine: css(".review-lead-heading h3", "line-height"),
      captionSize: css(".review-lead-heading > p:last-child", "font-size"),
      captionLine: css(".review-lead-heading > p:last-child", "line-height"),
      captionGap: css(".review-lead-heading", "gap"),
      noteLabel: css(".review-agent-note > p", "font-size"),
      noteSize: css(".review-agent-note > p + p", "font-size"),
      noteLine: css(".review-agent-note > p + p", "line-height"),
      noteGap: gap(".review-agent-note > p", ".review-agent-note > p + p"),
      summarySize: css(".comparison-lines", "font-size"),
      summaryLine: css(".comparison-lines", "line-height"),
      summaryTitle: css(".review-compared h3", "font-size"),
      summaryWeight: css(".review-compared h3", "font-weight"),
      summaryGap: css(".comparison-lines", "margin-top"),
      boundGap: css(".comparison-bound", "margin-top"),
      detailsGap: css(".review-details", "margin-top"),
      detailsHeight: css(".review-details > details > summary", "height"),
      fileHeight: css(".review-file-row", "height"),
      pathSize: css(".review-file-row > span", "font-size"),
      countsSize: css(".review-file-row > span + span", "font-size"),
      countsWeight: css(".review-file-row > span + span", "font-weight"),
      pairGap: css(".example-pair", "gap"),
      pairRadius: css(".example-pair > div", "border-radius"),
      pairPadding: css(".example-pair > div", "padding-top"),
      pairSize: css(".example-pair > div > code", "font-size"),
      pairLine: css(".example-pair > div > code", "line-height"),
      codeSize: css(".monaco-diff-editor .view-lines", "font-size"),
      codeLine: css(".monaco-diff-editor .view-lines", "line-height"),
      inputSize: css(".choice-example > code", "font-size"),
      inputLine: css(".choice-example > code", "line-height"),
      loneSize: css(".review-lone", "font-size"),
      loneLine: css(".review-lone", "line-height"),
      loneWeight: css(".review-lone", "font-weight"),
      iconSize: css(".partial-lines svg", "width"),
      iconGap: css(".partial-lines li", "column-gap"),
      partialGap: css(".partial-lines", "gap"),
      partialTop: css(".partial-lines", "margin-top"),
      cardBorder: css(".review-file-open", "border-left-width"),
      cardRadius: css(".review-file-open", "border-radius"),
      filePadding: css(".review-file-open .review-file-row", "padding-left"),
      fileGap: css(".review-file-row", "gap"),
      chevron: css(".review-file-row > svg", "width"),
      promptSize: css(".choice-prompt", "font-size"),
      promptWeight: css(".choice-prompt", "font-weight"),
      promptGap: css(".panel-choice-actions", "margin-top"),
      captionInputGap: gap(".choice-caption", ".choice-caption + .choice-example > code"),
      pairLabel: css(".example-pair > div > span", "font-size"),
    };
  });
}
const cases = [
  ["a", "2 choices need you", "1 of 2 answered", true],
  ["b", "1 caller behaves differently", "3 of 9 callers measured", false],
  ["c", "Partial evidence", "Read the diff before approving", false],
  [
    "d2",
    "Nothing was compared: compute_price could not run outside its test setup.",
    "The diff and tests are the evidence.",
    false,
  ],
  ["d2NoReason", "Nothing was compared.", "The diff and tests are the evidence.", false],
  [
    "d",
    "Nothing was compared for this change.",
    "Behavior and callers were not recorded for this revision. The diff and tests are the evidence.",
    false,
  ],
  ["merged", "Merged by your rules", "Merge mode: Merge alone when safe", false],
] as const;
it.each(cases)(
  "orders and measures %s at 1920",
  async (name, title, caption, closed) => {
    if (!browser) throw new Error("Missing browser");
    for (const [width, theme] of name === "a"
      ? ([
          [1920, "dark"],
          [1920, "day"],
          [1280, "dark"],
        ] as const)
      : name === "b"
        ? ([
            [1920, "dark"],
            [1920, "day"],
          ] as const)
        : ([[1920, "dark"]] as const)) {
      const page = await browser.newPage({ viewport: { width, height: 1080 } });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await page.waitForFunction(() => "mountHeader" in window);
        await page.evaluate(
          ({ name, theme, width }) => {
            document.documentElement.dataset.theme = theme;
            (window as unknown as { mountHeader: (name: string) => void }).mountHeader(name);
            const host = document.body.firstElementChild as HTMLElement;
            host.style.width = width === 1280 ? "608px" : "880px";
            host.style.height = "962px";
            (host.firstElementChild as HTMLElement).style.width = "100%";
          },
          { name, theme, width },
        );
        await page.locator(".review-lead").waitFor();
        await page.evaluate(() => document.fonts.ready);
        if (!closed) await page.locator(".monaco-diff-editor").waitFor();
        expect(await page.locator(".review-lead-heading h3, .review-lone").textContent()).toBe(
          title,
        );
        expect(await page.locator(".review-lead-heading > p:last-child").textContent()).toBe(
          caption,
        );
        const order = await page
          .locator(".review-body > *")
          .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
        expect(order).toEqual(
          name === "a" || name === "merged"
            ? ["Review lead", "Agent note", "What was compared", "Change files"]
            : name === "b" || name === "c"
              ? ["Review lead", "Agent note", "Change files", "What was compared"]
              : ["Review lead", "Agent note", "Change files"],
        );
        expect(await page.locator(".review-file-row").first().getAttribute("aria-expanded")).toBe(
          String(!closed),
        );
        expect(await page.locator(".review-details > details[open]").count()).toBe(0);
        expect(await page.locator(".review-compared").count()).toBe(
          name === "d" || name.startsWith("d2") ? 0 : 1,
        );
        expect(await page.locator(".review-decision-bar").count()).toBe(name === "merged" ? 0 : 1);
        if (name === "merged") {
          expect(await page.locator(".review-merged").textContent()).toBe(
            "Merged into main merge-p",
          );
          expect(await page.locator(".review-file-row").textContent()).not.toContain("Editable");
        }
        const actual = await measure(page);
        const target = {
          ...expected,
          fileHeight: closed ? 44 : 40,
          ...(width === 1280 ? { pairPadding: 9 } : {}),
        };
        for (const key of Object.keys(target) as (keyof typeof target)[])
          if (actual[key] !== null)
            expect(Math.abs((actual[key] ?? 0) - target[key]), key).toBeLessThanOrEqual(1);
        if (name === "b")
          expect(await page.locator(".same-callers").textContent()).toBe(
            "Cart.subtotals and Customer.quote: same result on 12 recorded calls each.",
          );
        if (process.env.REVIEW_LEAD_SHOTS === "1" && name !== "d2NoReason") {
          await mkdir(shots, { recursive: true });
          const key = `${name === "merged" ? "g" : name}${width === 1280 ? "-1280" : ""}-${theme}`;
          const product = await page
            .locator(".review-panel")
            .screenshot({ path: `${shots}${key}-panel.png` });
          const sketch = await readFile(
            new URL(`../../../docs/sketches/review/${key}.png`, import.meta.url),
          );
          const rows = Object.entries(actual)
            .filter(([, v]) => v !== null)
            .map(
              ([k, v]) =>
                `<tr><td>${k}</td><td>${String(v)}</td><td>${String(target[k as keyof typeof target])}</td></tr>`,
            )
            .join("");
          const report = await browser.newPage({ viewport: { width: width * 2, height: 1400 } });
          await report.setContent(
            `<style>body{margin:0;background:#eee;color:#111;font:16px sans-serif}.pair{display:flex;gap:24px}td{padding:2px 16px}img{object-fit:contain;object-position:top left}</style><p>Product · hand-written fixture / docs/sketches/review/${key}.png · DESIGN.md items 2–5</p><div class="pair"><img width="${String(width === 1280 ? 608 : 880)}" src="data:image/png;base64,${product.toString("base64")}"><img width="${String(width)}" src="data:image/png;base64,${sketch.toString("base64")}"></div><table><tr><th>Measurement</th><th>Product</th><th>DESIGN</th></tr>${rows}</table>`,
          );
          await report.screenshot({ path: `${shots}${key}-compare.png`, fullPage: true });
          await writeFile(
            `${shots}${key}.json`,
            JSON.stringify({ actual, expected: target }, null, 2),
          );
          await report.close();
        }
        if (name === "c") {
          expect(await page.locator(".partial-lines li").count()).toBe(4);
          expect(await page.locator(".partial-lines .choice-example").count()).toBe(1);
          expect(await page.locator(".partial-lines").textContent()).toContain(
            "Customer.quote: not measured, recording budget reached.",
          );
          expect(await page.locator(".comparison-lines").textContent()).toContain(
            "2 of 9 callers measured; 7 were not.",
          );
          expect(
            await page
              .locator(".partial-lines > li > svg")
              .evaluateAll((els) =>
                els.every(
                  (el) =>
                    getComputedStyle(el).color ===
                    getComputedStyle(document.querySelector(".review-lead-heading > p") as Element)
                      .color,
                ),
              ),
          ).toBe(true);
        }
        if (name === "d" || name.startsWith("d2")) {
          expect(await page.locator(".review-details").count()).toBe(0);
          expect(
            await page.locator(".review-diff-viewport").evaluate((el) => el.clientHeight),
          ).toBeGreaterThanOrEqual(400);
        }
        expect(
          await page
            .locator(".review-file-row")
            .first()
            .evaluate((el) => el.lastElementChild?.tagName.toLowerCase()),
        ).toBe("svg");
        if (name === "a" || name === "merged")
          expect(
            await page
              .locator(".review-details > details > summary")
              .evaluate((el) => el.lastElementChild?.tagName.toLowerCase()),
          ).toBe("svg");
        if (name === "a") {
          expect(await page.locator(".choice-caption").textContent()).toBe(
            "Inventory.reserve · from 24 compared inputs",
          );
          expect(await page.locator(".choice-prompt").textContent()).toBe("Keep this behavior?");
          expect(await page.locator(".answered-controls").textContent()).toBe("Yes, keep itChange");
        }
        expect(errors).toEqual([]);
      } finally {
        await page.close();
      }
    }
  },
  60000,
);

it.each([1280, 1920])(
  "preserves the body scroll, editor and focus under long live updates at %i",
  async (width) => {
    if (!browser) throw new Error("Missing browser");
    for (const recorded of [true, false]) {
      const page = await browser.newPage({ viewport: { width, height: 1080 } });
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await page.waitForFunction(() => "reviewTest" in window);
        await page.evaluate((recorded) => {
          window.reviewTest.mount(recorded, 20);
        }, recorded);
        await page.getByRole("button", { name: /^Decisions:/ }).click();
        const panel = page.getByRole("complementary", { name: "Review panel" });
        await panel.locator(".monaco-diff-editor").waitFor();
        const note = "Agent note. ".repeat(200);
        const publish = (summary: string) =>
          page.evaluate((summary) => {
            const proposal = window.reviewTest.state().changes["change-1"]?.proposal;
            if (!proposal) throw new Error("Missing proposal");
            window.reviewTest.emit({
              seq: 0,
              ts: "2026-10-30T18:00:00Z",
              type: "change.proposed",
              payload: { ...proposal, summary },
            });
          }, summary);
        await publish(note);
        const context = panel.getByRole("region", { name: "Review context", exact: true });
        await context.evaluate((el) => {
          el.scrollTop = 100;
        });
        const editor = await panel.locator(".monaco-diff-editor").elementHandle();
        const close = panel.getByRole("button", { name: "Close review" });
        await close.focus();
        await publish(note + "Updated.");
        expect(await close.evaluate((el) => document.activeElement === el)).toBe(true);
        expect(await context.evaluate((el) => el.scrollTop)).toBe(100);
        expect(
          await editor.evaluate((el) => el === document.querySelector(".monaco-diff-editor")),
        ).toBe(true);
        expect(await context.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
        expect(await panel.locator(".review-agent-note > p + p").textContent()).toBe(
          note + "Updated.",
        );
      } finally {
        await page.close();
      }
    }
  },
  60000,
);

it.each(["a", "b", "c"] as const)(
  "wraps long %s evidence losslessly at ordinary size and 200%% zoom",
  async (name) => {
    if (!browser) throw new Error("Missing browser");
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "headerState" in window);
      const token = "VeryLongQualifiedName".repeat(12);
      await page.evaluate(
        ({ name, token }) => {
          const api = window as unknown as Pick<
            typeof import("./ReviewHeader.fixture"),
            "mountHeader" | "headerState"
          >;
          const state = api.headerState(name);
          const id = name === "a" ? "change-3" : "change-1";
          const change = state.changes[id];
          if (!change?.proposal) throw new Error("Missing proposal");
          change.proposal.summary = `Updated ${token}. All tests pass.`;
          const facts = state.changeFacts[id]?.at(-1);
          for (const question of facts?.review_cards ?? []) {
            question.question = `Was this behavior change asked for? ${token}`;
            if (question.example) question.example.inputs = token;
          }
          const caller = facts?.measured_cone?.[0];
          const node = state.graph?.nodes.find((n) => n.id === caller?.node_id);
          if (node) node.qualname = token;
          for (const row of caller?.rows ?? []) {
            row.inputs = token;
            row.before = `before ${token}`;
            row.after = `after ${token}`;
          }
          api.mountHeader(name, state);
          (document.body.firstElementChild?.firstElementChild as HTMLElement).style.width = "100%";
        },
        { name, token },
      );
      await page.evaluate(() => document.fonts.ready);
      for (const zoom of [1, 2]) {
        await page.evaluate(
          ({ zoom, width }) => {
            document.body.style.zoom = String(zoom);
            (document.body.firstElementChild as HTMLElement).style.width =
              `${String(width / zoom)}px`;
          },
          { zoom, width: name === "c" ? 328 : 880 },
        );
        const overflow = await page
          .locator(".review-body")
          .evaluate((body) =>
            [body, ...body.querySelectorAll<HTMLElement>("h3, p, code")]
              .filter((el) => el.scrollWidth > el.clientWidth + 1)
              .map((el) => el.getAttribute("class") || el.tagName),
          );
        expect(overflow, `zoom ${String(zoom)}`).toEqual([]);
        expect(await page.locator(".review-agent-note > p + p").textContent()).toContain(token);
        expect(await page.locator(".choice-example > code").last().textContent()).toBe(token);
      }
    } finally {
      await page.close();
    }
  },
  60000,
);

it.each([1, 2])(
  "uses the compared input count's number in the panel caption: %i",
  async (count) => {
    if (!browser) throw new Error("Missing browser");
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "mountHeader" in window);
      await page.evaluate((count) => {
        const api = window as unknown as Pick<
          typeof import("./ReviewHeader.fixture"),
          "mountHeader" | "headerState"
        >;
        const state = api.headerState("a");
        const comparison = state.changeFacts["change-3"]?.at(-1)?.behavior_diff?.[0];
        if (!comparison) throw new Error("Missing comparison");
        comparison.compared_count = count;
        api.mountHeader("a", state);
      }, count);
      await page.locator(".choice-caption").waitFor();
      expect(await page.locator(".choice-caption").textContent()).toBe(
        `Inventory.reserve · from ${String(count)} compared ${count === 1 ? "input" : "inputs"}`,
      );
    } finally {
      await page.close();
    }
  },
);

it("keeps the keyboard focus outline inside an open diff card", async () => {
  if (!browser) throw new Error("Missing browser");
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "mountHeader" in window);
    await page.evaluate(() => {
      const api = window as unknown as Pick<typeof import("./ReviewHeader.fixture"), "mountHeader">;
      api.mountHeader("d");
    });
    const header = page.locator(".review-file-open > .review-file-row").first();
    await page.keyboard.press("Tab");
    await header.focus();
    const outline = await header.evaluate((el) => ({
      visible: el.matches(":focus-visible"),
      offset: getComputedStyle(el).outlineOffset,
      width: getComputedStyle(el).outlineWidth,
      style: getComputedStyle(el).outlineStyle,
    }));
    expect(outline).toEqual({ visible: true, offset: "-2px", width: "2px", style: "solid" });
  } finally {
    await page.close();
  }
});

it.each(["default", "opened", "closed"])(
  "recomputes d to a until a person chooses: %s",
  async (choice) => {
    if (!browser) throw new Error("Missing browser");
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "mountHeader" in window);
      await page.evaluate(() => {
        const api = window as unknown as Pick<
          typeof import("./ReviewHeader.fixture"),
          "mountHeader" | "headerState"
        > & {
          update: (
            state: ReturnType<(typeof import("./ReviewHeader.fixture"))["headerState"]>,
          ) => void;
        };
        const state = api.headerState("a");
        const facts = state.changeFacts["change-3"]?.at(-1);
        if (!facts) throw new Error("Missing facts");
        const proposal = state.changes["change-3"]?.proposal;
        if (proposal) proposal.summary = "Agent note. ".repeat(400);
        facts.review_cards = [];
        facts.behavior_diff = null;
        facts.measured_cone = null;
        api.update = api.mountHeader("a", state);
      });
      const file = page.locator(".review-file-row").first();
      await page.locator(".monaco-diff-editor").waitFor();
      if (choice !== "default") await file.click();
      if (choice === "opened") await file.click();
      await page.locator(".review-body").evaluate((el) => {
        el.scrollTop = 40;
      });
      const before = await page.locator(".review-body").evaluate((el) => el.scrollTop);
      expect(before).toBe(40);
      const close = page.getByRole("button", { name: "Close review" });
      await close.focus();
      await page.evaluate(() => {
        const api = window as unknown as {
          headerState: (typeof import("./ReviewHeader.fixture"))["headerState"];
          update: (
            state: ReturnType<(typeof import("./ReviewHeader.fixture"))["headerState"]>,
          ) => void;
        };
        const state = api.headerState("a");
        const proposal = state.changes["change-3"]?.proposal;
        if (proposal) proposal.summary = "Agent note. ".repeat(400);
        api.update(state);
      });
      expect(await file.getAttribute("aria-expanded")).toBe(String(choice === "opened"));
      expect(await close.evaluate((el) => document.activeElement === el)).toBe(true);
      expect(await page.locator(".review-body").evaluate((el) => el.scrollTop)).toBe(before);
    } finally {
      await page.close();
    }
  },
  60000,
);
