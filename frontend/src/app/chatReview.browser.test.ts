function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Missing fixture evidence");
  return value;
}
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type { GraphProbeHost } from "../graph";
import type {} from "./chatReview.browser.fixture";

let browser: Browser;
const entries: { server: ViteDevServer; html: string; url: string }[] = [];
let streams = 0;
beforeAll(async () => {
  for (const mode of ["development", "replay"]) {
    const server = await createServer({
      root: fileURLToPath(new URL("../../", import.meta.url)),
      mode,
      cacheDir: fileURLToPath(
        new URL(`../../../.shots/chat-review-${mode}-cache`, import.meta.url),
      ),
      server: { host: "127.0.0.1", port: 0, strictPort: false },
      plugins: [
        {
          name: "idle-C4-stream",
          configureServer(vite) {
            vite.middlewares.use("/events", (_req, res) => {
              streams++;
              res.writeHead(200, { "Content-Type": "text/event-stream" });
              res.write(": connected\n\n");
            });
          },
        },
      ],
    });
    const html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/chatReview.browser.fixture.tsx";</script></body></html>',
    );
    await server.listen();
    entries.push({ server, html, url: server.resolvedUrls?.local[0] ?? "" });
  }
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await Promise.all(entries.map((e) => e.server.close()));
});
const layout = (page: Page, name: string) =>
  page.getByRole("group", { name: "Workspace view" }).getByRole("button", { name, exact: true });
const panel = (page: Page) => page.getByRole("complementary", { name: "Review panel" });
const card = (page: Page) => page.getByRole("article", { name: "Proposed change" });
const comment = (page: Page) => panel(page).getByRole("textbox", { name: /Comment/ });
const approve = (page: Page) =>
  panel(page).getByRole("button", { name: "Approve and merge", exact: true });
async function open({ width = 1920, theme = "dark", app = false, replay = false } = {}) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : 800 },
    reducedMotion: "reduce",
  });
  page.setDefaultTimeout(15_000);
  const entry = required(entries[replay ? 1 : 0]);
  const requested: string[] = [],
    mutations: { path: string; body: unknown }[] = [];
  page.on("request", (req) => {
    requested.push(new URL(req.url()).pathname);
    if (req.method() === "POST")
      mutations.push({ path: new URL(req.url()).pathname, body: req.postDataJSON() as unknown });
  });
  await page.route("**/files?*", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    const content = await page.evaluate(
      ({ path }) =>
        window.chatReviewTest.state().changes["change-1"]?.files[path]?.file.modified ?? "",
      { path: params.get("path") ?? "" },
    );
    const hash = await page.evaluate(
      async (content) =>
        Array.from(
          new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content))),
          (b) => b.toString(16).padStart(2, "0"),
        ).join(""),
      content,
    );
    await route.fulfill({
      json: { path: params.get("path"), agent_id: params.get("agent"), content, hash },
    });
  });
  await page.route("**/changes/*/approve", (r) => r.fulfill({ status: 202, json: {} }));
  await page.route("**/", (r) => r.fulfill({ contentType: "text/html", body: entry.html }));
  await page.goto(entry.url);
  await page.waitForFunction(() => !!window.chatReviewTest);
  await page.evaluate(
    ({ app, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.chatReviewTest.mount(app);
    },
    { app, theme },
  );
  if (app && replay) {
    await check(page.getByRole("region", { name: "Replay controls" })).toBeVisible();
    await page.evaluate(() => {
      window.chatReviewTest.seed();
    });
  }
  await page.evaluate(() => document.fonts.ready);
  expect(requested.some((p) => /ReviewPanel|ReviewBody|ReviewDecisionBar/.test(p))).toBe(false);
  return { page, requested, mutations };
}
async function openAppReview(page: Page) {
  await page
    .locator(".shell-top-bar")
    .getByRole("button", { name: /Decisions/ })
    .click();
  await page
    .locator(".inbox-row")
    .filter({ has: page.getByLabel("Agent A", { exact: true }) })
    .getByRole("button", { name: /Review change/ })
    .click();
  await check(panel(page)).toBeVisible();
}
async function camera(page: Page) {
  return page.locator('[aria-label="Repository graph"]').evaluate((host: GraphProbeHost) => {
    const graph = host.oversightGraph;
    if (!graph) throw new Error("Missing graph");
    return { pan: graph.pan(), zoom: graph.zoom() };
  });
}
async function waitForColdGraph(page: Page) {
  await page.waitForFunction(() => {
    const graph = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    return !!graph && !graph.animating();
  });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            resolve();
          });
        }),
      ),
  );
}
async function abandonedCamera() {
  const { page } = await open();
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/OversightStage.tsx*", async (route) => {
    await gate;
    await route.continue();
  });
  try {
    await layout(page, "Oversight").click();
    await check(page.getByText("Loading repository graph…", { exact: true })).toBeVisible();
    await layout(page, "Chat").click();
    release();
    await waitForColdGraph(page);
    return await camera(page);
  } finally {
    release();
    await page.close();
  }
}
async function framingEvidence(page: Page) {
  return page.locator('[aria-label="Repository graph"]').evaluate((host: GraphProbeHost) => {
    const graph = host.oversightGraph;
    if (!graph) throw new Error("Missing graph");
    const viewport = host.getBoundingClientRect();
    const state = window.chatReviewTest.state();
    const proposal = state.changes["change-1"]?.proposal;
    if (!proposal) throw new Error("Missing proposal");
    const overlay = document
      .querySelector('[aria-label="Possibly affected"]')
      ?.getBoundingClientRect();
    const clear = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
    const pan = graph.pan(),
      zoom = graph.zoom(),
      bounds = graph.nodeBounds();
    const visible = (id: string) => {
      const box = bounds.find((b) => b.id === graph.representative(id));
      if (!box) return false;
      const x1 = box.x1 * zoom + pan.x,
        x2 = box.x2 * zoom + pan.x;
      const y1 = box.y1 * zoom + pan.y,
        y2 = box.y2 * zoom + pan.y;
      return (
        x1 >= -1 &&
        y1 >= -1 &&
        x2 <= viewport.width + 1 &&
        y2 <= (clear ? clear.bottom - viewport.top : viewport.height) + 1 &&
        (!overlay ||
          x2 <= overlay.left - viewport.left + 1 ||
          x1 >= overlay.right - viewport.left - 1 ||
          y2 <= overlay.top - viewport.top + 1 ||
          y1 >= overlay.bottom - viewport.top - 1)
      );
    };
    const rows = [...document.querySelectorAll('[aria-label="Possibly affected"] li[title]')];
    const measured = state.changeFacts["change-1"]?.at(-1)?.measured_cone ?? [];
    for (const { node_id } of measured) {
      if (!bounds.some((box) => box.id === graph.representative(node_id)))
        throw new Error(`Missing measured caller on canvas: ${node_id}`);
    }
    return {
      seeds: proposal.impact.seeds
        .filter((id) =>
          [
            ...(state.graph?.nodes ?? []),
            ...(state.changes["change-1"]?.graph.nodes_added ?? []),
          ].some((node) => node.id === id && !node.is_test),
        )
        .map((id) => ({ id, visible: visible(id) })),
      measured: measured.map(({ node_id }) => ({ id: node_id, visible: visible(node_id) })),
      callers: proposal.impact.entries.map(({ node_id }) => ({
        id: node_id,
        visible: visible(node_id),
        reportedVisible: !rows
          .find((row) => row.getAttribute("title") === node_id)
          ?.textContent.includes("Out of view"),
      })),
      pan,
      zoom,
    };
  });
}
async function framed(page: Page) {
  await page.waitForFunction(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const graph = host?.oversightGraph;
    return (
      !!graph &&
      graph.nodeCenters().length > 0 &&
      !graph.animating() &&
      host.getBoundingClientRect().width > 0
    );
  });
  // Read canvas geometry independently; the UI's omission label is checked afterwards.
  await check
    .poll(async () => (await framingEvidence(page)).seeds.every((s) => s.visible))
    .toBe(true);
  const result = await framingEvidence(page);
  expect(result.seeds.length).toBeGreaterThan(0);
  expect(result.measured).toHaveLength(3);
  expect(result.measured).toEqual([
    { id: "shop.cart:Cart.quote_add", visible: false },
    { id: "shop.cart:Cart.subtotals", visible: false },
    { id: "shop.models:Customer.quote", visible: false },
  ]);
  expect(result.callers.find((caller) => caller.id === "shop.pricing:price_breaks")?.visible).toBe(
    true,
  );
  await check
    .poll(async () =>
      (await framingEvidence(page)).callers.every(
        (caller) => caller.visible === caller.reportedVisible,
      ),
    )
    .toBe(true);
  return result;
}
it.each([1920, 1280])(
  "keeps one thread/review, echo, tabs, draft and comment at %ipx",
  async (width) => {
    const { page, requested } = await open({ width });
    try {
      const draft = page.getByRole("textbox", { name: "Message Agent A" });
      await page.evaluate(() => {
        window.chatReviewTest.draft("Retain my draft");
      });
      const composer = await draft.elementHandle();
      await card(page).getByRole("button", { name: "Review change", exact: true }).click();
      await check(panel(page)).toBeVisible();
      await comment(page).fill("Retain review comment");
      const review = await panel(page).elementHandle();
      if (width === 1280) {
        const tabs = page.getByRole("tablist", { name: "Conversation and review" });
        await tabs.getByRole("tab", { name: "Agent A · conversation" }).click();
        await check(panel(page)).toBeHidden();
        await check(draft).toHaveValue("Retain my draft");
        await check(card(page)).toContainText("Reviewing in the panel");
        await tabs.getByRole("tab", { name: "Agent A · conversation" }).press("ArrowRight");
        await check(tabs.getByRole("tab", { name: "Review · ready" })).toHaveAttribute(
          "aria-selected",
          "true",
        );
      } else await check(card(page)).toContainText("Reviewing in the panel");
      await check(comment(page)).toHaveValue("Retain review comment");
      expect(requested.some((p) => /GraphView|DotField|OversightStage/.test(p))).toBe(false);
      await layout(page, "Oversight").click();
      await page.waitForFunction(() =>
        Boolean(
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.nodeCenters().length,
        ),
      );
      await layout(page, "Chat").click();
      await check(comment(page)).toHaveValue("Retain review comment");
      expect(
        await review.evaluate((el) => el === document.querySelector('[aria-label="Review panel"]')),
      ).toBe(true);
      await panel(page).getByRole("button", { name: "Close review" }).click();
      await check(
        card(page).getByRole("button", { name: "Review change", exact: true }),
      ).toBeVisible();
      await check(draft).toHaveValue("Retain my draft");
      expect(
        await composer.evaluate(
          (el) => el === document.querySelector('[aria-label="Message Agent A"]'),
        ),
      ).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    } finally {
      await page.close();
    }
  },
);
it("cold production Chat review loads its editor without requesting a graph", async () => {
  const { page, requested } = await open({ app: true });
  try {
    await openAppReview(page);
    await check(panel(page).getByLabel("Editable review file")).toHaveAttribute(
      "data-state",
      "clean",
    );
    expect(requested.some((p) => /GraphView|DotField|OversightStage/.test(p))).toBe(false);
    await check(approve(page)).toBeEnabled();
  } finally {
    await page.close();
  }
});
it.each([false, true])(
  "production App uses identical decision target in both layouts (replay=%s)",
  async (replay) => {
    const { page, mutations } = await open({ app: true, replay });
    try {
      const first = streams;
      if (!replay) {
        await layout(page, "Oversight").click();
        await page.waitForFunction(() =>
          Boolean(
            document
              .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph?.nodeCenters().length,
          ),
        );
        await layout(page, "Chat").click();
      }
      await openAppReview(page);
      await check(
        page.getByRole("region", { name: "Agent A conversation", exact: true }),
      ).toBeVisible();
      if (replay) {
        await check(approve(page)).toBeDisabled();
        expect(mutations).toEqual([]);
        return;
      }
      await check(approve(page)).toBeEnabled();
      await comment(page).fill("Same candidate");
      for (const key of ["Enter", "Space", "Control+Enter"]) {
        await approve(page).focus();
        await page.keyboard.press(key);
        await comment(page).focus();
        await page.keyboard.press(key);
      }
      expect(mutations).toEqual([]);
      await comment(page).fill("Same candidate");
      await page.route("**/changes/change-1/approve", (r) =>
        r.fulfill({
          status: 409,
          json: { error: { code: "wrong_state", message: "Fixture refusal" } },
        }),
      );
      await approve(page).click();
      await check(panel(page).getByText("Fixture refusal", { exact: false })).toBeVisible();
      await layout(page, "Oversight").click();
      await framed(page);
      await check(comment(page)).toHaveValue("Same candidate");
      await page.route("**/changes/change-1/approve", (r) => r.fulfill({ status: 202, json: {} }));
      await approve(page).click();
      await check(approve(page)).toBeDisabled();
      expect(mutations).toHaveLength(2);
      expect(mutations[0]).toEqual(mutations[1]);
      const proposal = await page.evaluate(
        () => window.chatReviewTest.state().changes["change-1"]?.proposal,
      );
      expect(mutations[0]).toMatchObject({
        path: "/changes/change-1/approve",
        body: {
          revision: proposal?.revision,
          head_commit: proposal?.head_commit,
          comment: "Same candidate",
        },
      });
      expect(streams).toBe(first);
    } finally {
      await page.close();
    }
  },
);
it("keeps answers, loading/dirty guards, selected diff and conflict in the shared App at 1280px", async () => {
  const { page, mutations } = await open({ app: true, width: 1280 });
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  try {
    await layout(page, "Oversight").click();
    await page.waitForFunction(() =>
      Boolean(
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.nodeCenters().length,
      ),
    );
    await page
      .getByRole("article", { name: "Agent B", exact: true })
      .getByRole("button", { name: "Conversation", exact: true })
      .click();
    await layout(page, "Chat").click();
    await page.evaluate(() => {
      window.chatReviewTest.questions();
    });
    await page.route("**/files?*", async (route) => {
      await gate;
      await route.fallback();
    });
    await openAppReview(page);
    await check(approve(page)).toBeDisabled();
    await panel(page).getByRole("button", { name: "Yes, keep it", exact: true }).click();
    await panel(page)
      .getByRole("button", { name: /shop\/pricing.py/ })
      .click();
    const editor = panel(page)
      .locator(".review-diff-viewport:not([hidden])")
      .getByLabel("Editable review file");
    await check(editor).toHaveAttribute("data-state", "loading");
    await check(approve(page)).toBeDisabled();
    release();
    await check(editor).toHaveAttribute("data-state", "clean");
    await check(approve(page)).toBeEnabled();
    await comment(page).fill("Keep the selected file and answer");
    const input = panel(page).locator(
      ".review-diff-viewport:not([hidden]) .editor.modified :is(.native-edit-context, textarea.inputarea)",
    );
    await input.focus();
    await page.keyboard.press("Control+End");
    await page.keyboard.insertText("\n# review draft\n");
    await check(editor).toHaveAttribute("data-state", "dirty");
    await check(approve(page)).toBeDisabled();
    const tabs = page.getByRole("tablist", { name: "Conversation and review" });
    await tabs.getByRole("tab", { name: "Agent A · conversation", exact: true }).click();
    await check(
      page.getByRole("region", { name: "Agent A conversation", exact: true }),
    ).toBeVisible();
    await tabs.getByRole("tab", { name: "Review · ready", exact: true }).click();
    await check(editor).toHaveAttribute("data-state", "dirty");
    await check(comment(page)).toHaveValue("Keep the selected file and answer");
    await panel(page).getByRole("button", { name: "Discard edits", exact: true }).click();
    await check(approve(page)).toBeEnabled();
    await page.evaluate(() => {
      const s = window.chatReviewTest.state(),
        p = s.changes["change-1"]?.proposal;
      if (!p) throw new Error("Missing proposal");
      window.chatReviewTest.emit({
        seq: 0,
        ts: s.clock ?? "2026-11-01T22:40:00Z",
        type: "change.resolved",
        payload: {
          change_id: p.change_id,
          agent_id: p.agent_id,
          revision: p.revision,
          head_commit: p.head_commit,
          decision: "approved",
          merge: "conflict",
          merge_commit: null,
          reason: null,
          comment: null,
          conflict: { kind: "merge", paths: ["shop/pricing.py"], tests: null },
          decided_by: "person",
          review_seq: null,
          requested_by: null,
          reasons: [],
          assessment: null,
          grants: [],
          answers: [],
        },
      });
    });
    await check(approve(page)).toBeDisabled();
    await check(
      panel(page).getByRole("button", { name: "Request changes", exact: true }),
    ).toBeEnabled();
    for (const control of await panel(page).locator("button, textarea").all()) {
      if (await control.isVisible()) {
        const box = await control.boundingBox();
        expect(box && box.x >= 316 && box.x + box.width <= 1280).toBe(true);
      }
    }
    expect(mutations).toEqual([]);
  } finally {
    release();
    await page.close();
  }
});
it.each(["dark", "day"])("distinguishes every evidence result in %s mode", async (theme) => {
  const { page } = await open({ theme });
  try {
    await page.evaluate(() => {
      const assessment = window.chatReviewTest
        .state()
        .decisionEvents.findLast((event) => event.type === "change.assessed");
      if (assessment?.type !== "change.assessed") throw new Error("Missing assessment");
      const [first, , ...rest] = assessment.payload.rules;
      if (!first) throw new Error("Missing rule");
      window.chatReviewTest.emit({
        ...assessment,
        payload: { ...assessment.payload, rules: [{ ...first, result: "fail" }, ...rest] },
      });
    });
    const results = card(page).getByRole("list", { name: "Six recorded rule results" });
    await check(results.getByRole("listitem")).toHaveCount(6);
    await check(results.locator('[data-result="absent"]')).toHaveText(
      "Second-model review: not assessed",
    );
    await check(results.locator('[data-result="unknown"]')).toHaveText("Known spend: unknown");
    await check(card(page)).toContainText("Evidence 3 of 6");
    const indicators = await results.locator("li").evaluateAll((items) =>
      items.map((item) => {
        const icon = item.querySelector("svg");
        const style = getComputedStyle(item);
        return {
          result: item.dataset.result,
          glyph: icon?.innerHTML,
          hidden: icon ? getComputedStyle(icon).display === "none" : true,
          fill: style.backgroundColor,
          title: item.getAttribute("title"),
          width: item.getBoundingClientRect().width,
          height: item.getBoundingClientRect().height,
        };
      }),
    );
    expect(new Set(indicators.map((item) => item.glyph)).size).toBe(4);
    expect(indicators.every((item) => item.glyph && !item.hidden)).toBe(true);
    expect(indicators.every((item) => item.width === 9 && item.height === 9)).toBe(true);
    expect(indicators.find((item) => item.result === "absent")?.fill).not.toBe(
      indicators.find((item) => item.result === "unknown")?.fill,
    );
    expect(indicators.find((item) => item.result === "absent")?.title).toContain("not assessed");
  } finally {
    await page.close();
  }
});
it.each([1920, 1280])(
  "C5's resolved tab retains its owner and tab rows at %ipx without a graph request",
  async (width) => {
    const { page, requested } = await open({ width });
    try {
      await page.evaluate(() => {
        window.chatReviewTest.merged();
      });
      await check(card(page)).not.toContainText("Review is unavailable for this candidate.");
      await page.getByRole("button", { name: "Open its review", exact: true }).click();
      await check(page.getByRole("tab", { name: /Review · change-1/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await check(panel(page).getByText("Merged", { exact: true })).toBeVisible();
      const conversation = page.getByRole("region", { name: "Agent A conversation", exact: true });
      const focusTabs = page.getByRole("tablist", { name: "Open files and changes" });
      await check(focusTabs).toBeVisible();
      if (width === 1280) {
        const tabs = page.getByRole("tablist", { name: "Conversation and review" });
        const reviewTab = tabs.getByRole("tab", { name: "Review · merged", exact: true });
        await check(tabs).toBeVisible();
        await check(reviewTab).toHaveAttribute("aria-selected", "true");
        await check(conversation).toBeHidden();
        const upper = required(await focusTabs.boundingBox());
        const lower = required(await tabs.boundingBox());
        expect(upper.y + upper.height).toBeLessThanOrEqual(lower.y);
        expect(lower.height).toBe(40);
        await tabs.getByRole("tab", { name: "Agent A · conversation", exact: true }).click();
        await check(conversation).toBeVisible();
        await check(panel(page)).toBeHidden();
        await check(focusTabs.getByRole("tab", { name: /Review · change-1/ })).toHaveAttribute(
          "aria-selected",
          "true",
        );
        await reviewTab.click();
        await check(panel(page).getByText("Merged", { exact: true })).toBeVisible();
        await check(conversation).toBeHidden();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      } else await check(conversation).toBeVisible();
      expect(requested.some((p) => /GraphView|OversightStage/.test(p))).toBe(false);
    } finally {
      await page.close();
    }
  },
);
// Measured on an archive of cbba4cd0 with the recorded change-1 proposal:
// the aside fills the focus panel's content box (one pixel inside its border).
it.each([
  [1920, 880, 879],
  [1600, 880, 879],
  [1280, 608, 607],
])(
  "preserves the cbba4cd0 Oversight review width at %ipx",
  async (width, focusWidth, reviewWidth) => {
    const { page } = await open({ width });
    try {
      await card(page).getByRole("button", { name: "Review change", exact: true }).click();
      await check(panel(page).locator(".review-body")).toBeVisible();
      await layout(page, "Oversight").click();
      await check(panel(page)).toBeVisible();
      expect(required(await page.locator(".focus-panel").boundingBox()).width).toBe(focusWidth);
      expect(required(await panel(page).boundingBox()).width).toBe(reviewWidth);
    } finally {
      await page.close();
    }
  },
);
it("preserves the Oversight conversation width inside C4 wrappers at 1600px", async () => {
  const { page } = await open({ width: 1600 });
  try {
    await layout(page, "Oversight").click();
    const thread = page.locator('.focus-panel [aria-label="Agent A conversation"]');
    await check(thread).toBeVisible();
    expect(required(await page.locator(".focus-panel").boundingBox()).width).toBe(880);
    expect(required(await page.locator(".chat-review-conversation").boundingBox()).width).toBe(879);
    expect(required(await thread.boundingBox()).width).toBe(879);
  } finally {
    await page.close();
  }
});
it("retains the Oversight review cone after a run swap with no See on graph click", async () => {
  const { page } = await open();
  try {
    await card(page).getByRole("button", { name: "Review change", exact: true }).click();
    await layout(page, "Oversight").click();
    await framed(page);
    await check(page.getByRole("region", { name: "Possibly affected", exact: true })).toBeVisible();
    await page.evaluate(() => {
      const state = window.chatReviewTest.state();
      if (!state.run) throw new Error("Missing run");
      window.chatReviewTest.emit({
        seq: 1,
        ts: state.clock ?? "2026-11-01T22:40:00Z",
        type: "run.started",
        payload: { ...state.run, run_id: "replacement-run" },
      });
      // Restore retained events as replay does; leave the person's View untouched throughout.
      window.chatReviewTest.seed();
    });
    await check(layout(page, "Oversight")).toHaveAttribute("aria-pressed", "true");
    await check(panel(page)).toBeVisible();
    await check(page.getByRole("region", { name: "Possibly affected", exact: true })).toBeVisible();
  } finally {
    await page.close();
  }
});
it("cold and visited See on graph frame the cone, repeat a fresh frame and retain one graph", async () => {
  const { page } = await open();
  try {
    await card(page).getByRole("button", { name: "See on graph", exact: true }).click();
    await framed(page);
    const graph = await page.locator('[aria-label="Repository graph"]').elementHandle();
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    const zoom = await page
      .locator('[aria-label="Repository graph"]')
      .evaluate((el: GraphProbeHost) => el.oversightGraph?.zoom());
    await layout(page, "Chat").click();
    await page
      .locator(".chat-review-graph")
      .getByRole("button", { name: "See on graph", exact: true })
      .click();
    const next = await framed(page);
    expect(next.zoom).not.toBe(zoom);
    expect(
      await graph.evaluate(
        (el) => el === document.querySelector('[aria-label="Repository graph"]'),
      ),
    ).toBe(true);
  } finally {
    await page.close();
  }
});
it.each(["stage", "field", "field failure", "failure", "abandoned", "replacement", "run"])(
  "handles %s during cold framing without late navigation or focus",
  async (scenario) => {
    const before =
      scenario === "abandoned" || scenario === "replacement"
        ? await abandonedCamera()
        : { pan: { x: 0, y: 0 }, zoom: 1 };
    const { page } = await open();
    let release = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route(
      scenario.startsWith("field") ? "**/field/controller.ts*" : "**/OversightStage.tsx*",
      async (route) => {
        await gate;
        if (scenario.endsWith("failure")) await route.abort("failed");
        else await route.continue();
      },
    );
    try {
      await card(page).getByRole("button", { name: "See on graph", exact: true }).click();
      await check(page.getByText("Loading repository graph…", { exact: true })).toBeVisible();
      if (scenario === "abandoned" || scenario === "replacement" || scenario === "run") {
        await layout(page, "Chat").click();
        await layout(page, "Chat").focus();
        if (scenario === "replacement")
          await page.evaluate(() => {
            const s = window.chatReviewTest.state();
            const p = s.changes["change-1"]?.proposal;
            if (!p) throw new Error("Missing proposal");
            window.chatReviewTest.emit({
              seq: 0,
              ts: s.clock ?? "2026-11-01T22:40:00Z",
              type: "change.proposed",
              payload: { ...p, revision: 2, head_commit: "replacement" },
            });
          });
      }
      if (scenario === "run")
        await page.evaluate(() => {
          const s = window.chatReviewTest.state();
          if (!s.run) throw new Error("Missing run");
          window.chatReviewTest.emit({
            seq: 1,
            ts: s.clock ?? "2026-11-01T22:40:00Z",
            type: "run.started",
            payload: { ...s.run, run_id: "replacement-run" },
          });
        });
      if (scenario === "run")
        expect(await page.evaluate(() => window.chatReviewTest.state().run?.run_id)).toBe(
          "replacement-run",
        );
      release();
      if (scenario === "failure")
        await check(page.getByRole("alert")).toContainText("could not load");
      else if (scenario !== "abandoned" && scenario !== "replacement" && scenario !== "run")
        await framed(page);
      else {
        await page.waitForFunction(
          () => !!document.querySelector('[aria-label="Repository graph"]'),
        );
        await check(layout(page, "Chat")).toHaveAttribute("aria-pressed", "true");
        await check(layout(page, "Chat")).toBeFocused();
        await waitForColdGraph(page);
        // Same cold startup as ordinary abandoned entry; no discarded cone camera.
        expect(await camera(page)).toEqual(before);
      }
    } finally {
      release();
      await page.close();
    }
  },
);
it("retains an earlier timeline position while narrow tabs hide the mounted thread", async () => {
  const { page } = await open({ width: 1280 });
  try {
    await card(page).getByRole("button", { name: "Review change", exact: true }).click();
    const tabs = page.getByRole("tablist", { name: "Conversation and review" });
    await tabs.getByRole("tab", { name: "Agent A · conversation", exact: true }).click();
    await page.evaluate(() => {
      const ts = window.chatReviewTest.state().clock ?? "2026-11-01T22:40:00Z";
      for (let i = 0; i < 30; i++)
        window.chatReviewTest.emit({
          seq: 0,
          ts,
          type: "agent.message",
          payload: {
            agent_id: "agent-1",
            text: "Hand-written message for reading earlier entries.",
          },
        });
    });
    const timeline = page.getByRole("list", { name: "Conversation timeline" });
    await timeline.evaluate((el) => {
      el.scrollTop = 40;
      el.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
    await tabs.getByRole("tab", { name: "Review · ready", exact: true }).click();
    await page.evaluate(() => {
      window.chatReviewTest.emit({
        seq: 0,
        ts: "2026-11-01T22:42:00Z",
        type: "agent.message",
        payload: { agent_id: "agent-1", text: "Later hand-written message." },
      });
    });
    await tabs.getByRole("tab", { name: "Agent A · conversation", exact: true }).click();
    expect(await timeline.evaluate((el) => el.scrollTop)).toBe(40);
  } finally {
    await page.close();
  }
});
it("incoming proposal, assessment, usage and resolution never select a layout, tab or focus", async () => {
  const { page, requested } = await open();
  try {
    const input = page.getByRole("textbox", { name: "Message Agent A" });
    await page.evaluate(() => {
      window.chatReviewTest.draft("Keep reading");
    });
    await input.focus();
    await page.evaluate(() => {
      const s = window.chatReviewTest.state();
      const p = s.changes["change-1"]?.proposal;
      if (!p) throw new Error("Missing proposal");
      window.chatReviewTest.emit({
        seq: 0,
        ts: s.clock ?? "2026-11-01T22:40:00Z",
        type: "change.proposed",
        payload: { ...p, revision: 2, head_commit: "next" },
      });
      const a = s.decisionEvents.find((e) => e.type === "change.assessed");
      if (!a) throw new Error("Missing assessment");
      window.chatReviewTest.emit({ ...a, seq: 0 });
      window.chatReviewTest.emit({
        seq: 0,
        ts: s.clock ?? "2026-11-01T22:40:00Z",
        type: "usage.tick",
        payload:
          s.usage ??
          (() => {
            throw new Error("Missing usage");
          })(),
      });
      window.chatReviewTest.merged();
    });
    await check(input).toBeFocused();
    await check(input).toHaveValue("Keep reading");
    await check(layout(page, "Chat")).toHaveAttribute("aria-pressed", "true");
    await check(panel(page)).toHaveCount(0);
    expect(requested.some((p) => /GraphView|OversightStage/.test(p))).toBe(false);
  } finally {
    await page.close();
  }
});
it("keeps the proposal card top highlight in both themes and desktop widths", async () => {
  for (const width of [1920, 1280])
    for (const theme of ["dark", "day"]) {
      const { page } = await open({ width, theme });
      try {
        await check(card(page)).toBeVisible();
        const shadow = await card(page).evaluate((element) => getComputedStyle(element).boxShadow);
        expect.soft(shadow, `${theme} at ${String(width)}px`).not.toBe("none");
      } finally {
        await page.close();
      }
    }
});
it.runIf(process.env.CHAT_REVIEW_SHOTS === "1")(
  "captures eight retained C4 states and computed geometry",
  async () => {
    const directory = fileURLToPath(new URL("../../../.shots/chat-review/", import.meta.url));
    await mkdir(directory, { recursive: true });
    const measures = [];
    for (const width of [1920, 1280])
      for (const theme of ["dark", "day"]) {
        const { page } = await open({ width, theme });
        try {
          for (const state of ["proposed", "review"]) {
            if (state === "proposed")
              await check(
                card(page).getByRole("button", { name: "Review change", exact: true }),
              ).toBeVisible();
            if (state === "review") {
              // Retained-state capture: visit explicitly; cold review is tested separately.
              await layout(page, "Oversight").click();
              await page.waitForFunction(() =>
                Boolean(
                  document
                    .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                    ?.oversightGraph?.nodeCenters().length,
                ),
              );
              await layout(page, "Chat").click();
              await card(page).getByRole("button", { name: "Review change", exact: true }).click();
              await check(panel(page).locator(".review-body")).toBeVisible();
              await check(panel(page).locator(".monaco-diff-editor").first()).toBeVisible();
            }
            await page.evaluate(() => document.fonts.ready);
            await page.evaluate(
              () =>
                new Promise<void>((r) =>
                  requestAnimationFrame(() =>
                    requestAnimationFrame(() => {
                      r();
                    }),
                  ),
                ),
            );
            const name = `${state}-${String(width)}-${theme}.png`;
            await page.screenshot({ path: directory + name });
            measures.push({
              name,
              ...(await page.evaluate(() => {
                const box = (s: string) => {
                  const el = document.querySelector(s);
                  if (!el) return null;
                  const b = el.getBoundingClientRect(),
                    c = getComputedStyle(el);
                  return {
                    x: b.x,
                    y: b.y,
                    width: b.width,
                    height: b.height,
                    padding: c.padding,
                    margin: c.margin,
                    borderRadius: c.borderRadius,
                    gap: c.gap,
                    columns: c.gridTemplateColumns,
                    fontSize: c.fontSize,
                    fontWeight: c.fontWeight,
                    lineHeight: c.lineHeight,
                  };
                };
                return {
                  rail: box(".left-rail"),
                  agents: box(".chat-agents"),
                  conversation: box(".chat-review-conversation"),
                  where: box(".chat-work"),
                  cap: box(".conversation-entries"),
                  card: box(".chat-change-card"),
                  header: box(".chat-change-card header"),
                  chips: box(".chat-change-chips"),
                  title: box(".chat-change-card header strong"),
                  cost: box(".chat-change-cost"),
                  lead: box(".chat-comparison-lead strong"),
                  caller: box(".chat-caller code"),
                  inputs: box(".chat-example-input"),
                  cell: box(".chat-example-pair > div"),
                  indicators: box(".chat-change-evidence ul"),
                  chip: box(".chat-change-chips li"),
                  comparison: box(".chat-change-comparison"),
                  pair: box(".chat-example-pair"),
                  evidence: box(".chat-change-evidence"),
                  indicator: box(".chat-change-evidence li"),
                  footer: box(".chat-change-card footer"),
                  action: box(".chat-change-card footer button"),
                  echo: box(".chat-change-echo"),
                  reviewing: box(".chat-reviewing"),
                  review: box(".chat-review-panel"),
                  tabs: box(".chat-review-tabs"),
                };
              })),
            });
          }
        } finally {
          await page.close();
        }
      }
    await writeFile(directory + "measurements.json", JSON.stringify(measures, null, 2));
  },
  120_000,
);
