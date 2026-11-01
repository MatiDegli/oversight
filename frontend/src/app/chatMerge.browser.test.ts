import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./chatMerge.browser.fixture";
import type { GraphProbeHost } from "../graph";
import type { ChangeResolved } from "@contracts";

let browser: Browser;
const entries: { server: ViteDevServer; html: string; url: string }[] = [];
beforeAll(async () => {
  for (const mode of ["development", "replay"]) {
    const server = await createServer({
      root: fileURLToPath(new URL("../../", import.meta.url)),
      mode,
      cacheDir: fileURLToPath(new URL(`../../../.shots/chat-merge-${mode}-cache`, import.meta.url)),
      plugins: [
        {
          name: "idle-C5-stream",
          configureServer(vite) {
            vite.middlewares.use("/events", (_req, res) => {
              res.writeHead(200, { "Content-Type": "text/event-stream" });
              res.write(": connected\n\n");
            });
          },
        },
      ],
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    const html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/chatMerge.browser.fixture.tsx";</script></body></html>',
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
const chip = (page: Page) => page.locator("button.chat-merge-chip");
const receipt = (page: Page) => page.getByRole("article", { name: "Merged by your rules" });
const layout = (page: Page, name: string) =>
  page.getByRole("group", { name: "Workspace view" }).getByRole("button", { name, exact: true });
async function open({
  merged = true,
  app = false,
  replay = false,
  width = 1920,
  theme = "dark",
} = {}) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : 800 },
    reducedMotion: "reduce",
  });
  const entry = entries[replay ? 1 : 0];
  if (!entry) throw new Error("Missing server");
  const mutations: string[] = [],
    requests: string[] = [];
  page.on("request", (req) => {
    requests.push(new URL(req.url()).pathname);
    if (!["GET", "HEAD"].includes(req.method())) mutations.push(req.url());
  });
  await page.route("**/", (r) => r.fulfill({ contentType: "text/html", body: entry.html }));
  await page.goto(entry.url);
  await page.waitForFunction(() => !!window.chatMergeTest);
  await page.evaluate(
    ({ merged, app, replay, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.chatMergeTest.mount(merged, app, !replay);
    },
    { merged, app, replay, theme },
  );
  if (app && replay) {
    await check(page.getByRole("region", { name: "Replay controls" })).toBeVisible();
    await page.evaluate((merged) => {
      window.chatMergeTest.seed(merged);
    }, merged);
  }
  await page.evaluate(() => document.fonts.ready);
  return { page, mutations, requests };
}
it.each([false, true])(
  "production App chips and resolved review navigate without commands (replay=%s)",
  async (replay) => {
    const { page, mutations } = await open({ merged: false, app: true, replay });
    try {
      await chip(page).click();
      await check(page.getByRole("region", { name: "Merge mode", exact: true })).toBeVisible();
      if (replay)
        for (const button of await page
          .getByRole("region", { name: "Merge mode", exact: true })
          .getByRole("button")
          .all())
          await check(button).toBeDisabled();
      await page.getByRole("button", { name: "Close tab Settings", exact: true }).click();
      await page.evaluate(() => {
        window.chatMergeTest.seed();
      });
      await layout(page, "Oversight").click();
      await page.waitForFunction(
        () =>
          !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph,
      );
      await page
        .getByRole("article", { name: "Agent A", exact: true })
        .getByRole("button", { name: "Conversation", exact: true })
        .click();
      await layout(page, "Chat").click();
      await check(receipt(page)).toBeVisible();
      await chip(page).click();
      await check(page.getByRole("region", { name: "Merge mode", exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Close tab Settings", exact: true }).click();
      const historical = await receipt(page).innerText();
      const graph = page.getByLabel("Repository graph", { exact: true });
      const camera = await graph.evaluate((el: GraphProbeHost) => ({
        zoom: el.oversightGraph?.zoom(),
        pan: el.oversightGraph?.pan(),
      }));
      await chip(page).focus();
      const control = await chip(page).elementHandle();
      await page.evaluate(() => {
        window.chatMergeTest.mode("ask_me");
      });
      await check(chip(page)).toHaveText("Merge mode: Ask me");
      await check(chip(page)).toBeFocused();
      expect(
        await control.evaluate((el) => el === document.querySelector("button.chat-merge-chip")),
      ).toBe(true);
      expect(await receipt(page).innerText()).toBe(historical);
      await check(layout(page, "Chat")).toHaveAttribute("aria-pressed", "true");
      expect(
        await graph.evaluate((el: GraphProbeHost) => ({
          zoom: el.oversightGraph?.zoom(),
          pan: el.oversightGraph?.pan(),
        })),
      ).toEqual(camera);
      await receipt(page).getByRole("button", { name: "Open its review" }).click();
      await check(page.getByRole("tab", { name: /Review · change-1/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await check(
        page.getByRole("complementary", { name: "Review panel", exact: true }),
      ).toBeVisible();
      await check(page.getByRole("tab", { name: /Review · change-3/ })).toHaveCount(0);
      await check(page.getByText("Merged into main", { exact: false }).first()).toBeVisible();
      await check(page.getByRole("button", { name: /^Approve/ })).toHaveCount(0);
      await check(layout(page, "Chat")).toHaveAttribute("aria-pressed", "true");
      await page.getByRole("button", { name: /^Close tab Review · change-1/ }).click();
      await check(receipt(page)).toBeVisible();
      expect(mutations).toEqual([]);
    } finally {
      await page.close();
    }
  },
);
it("does not approve by keyboard after the opened review receives a new proposal", async () => {
  const { page, mutations } = await open({ app: true });
  try {
    await page.route("**/files?*", (route) =>
      route.fulfill({
        json: {
          path: "shop/pricing.py",
          agent_id: "agent-1",
          content: "# C5 fixture\n",
          hash: "a".repeat(64),
        },
      }),
    );
    await page.route("**/changes/change-1/approve", (route) => route.fulfill({ status: 202 }));
    await layout(page, "Oversight").click();
    await page.waitForFunction(
      () =>
        !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
    );
    await page
      .getByRole("article", { name: "Agent A", exact: true })
      .getByRole("button", { name: "Conversation", exact: true })
      .click();
    await layout(page, "Chat").click();
    await receipt(page).getByRole("button", { name: "Open its review" }).click();
    await check(page.getByRole("tab", { name: /Review · change-1/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await page.evaluate(() => {
      window.chatMergeTest.repropose();
    });
    const approve = page.getByRole("button", { name: "Approve and merge", exact: true });
    await check(approve).toBeEnabled();
    await approve.focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Control+Enter");
    await check(approve).toBeFocused();
    await check(
      page.getByText("Approve with a click: it merges into main", { exact: true }),
    ).toBeVisible();
    expect(mutations).toEqual([]);
    await approve.click();
    await check.poll(() => mutations.length).toBe(1);
    expect(mutations[0]).toContain("/changes/change-1/approve");
  } finally {
    await page.close();
  }
});
it("keeps Chat-first graph loading, draft identity, focus and scroll ownership", async () => {
  const { page, requests } = await open();
  try {
    await check(receipt(page)).toBeVisible();
    expect(requests.filter((p) => /GraphView|DotField|cytoscape|OversightStage/.test(p))).toEqual(
      [],
    );
    const input = page.getByRole("textbox", { name: "Message Agent A" });
    await input.fill("Retained draft");
    await layout(page, "Oversight").click();
    await layout(page, "Chat").click();
    await check(input).toHaveValue("Retained draft");
    const timeline = page.getByRole("list", { name: "Conversation timeline" });
    await page.evaluate(() => {
      window.chatMergeTest.messages(45);
    });
    await timeline.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
      el.dispatchEvent(new Event("scroll"));
    });
    await page.evaluate(() => {
      window.chatMergeTest.messages(1);
    });
    expect(
      await timeline.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight),
    ).toBeLessThanOrEqual(2);
    await timeline.focus();
    await timeline.evaluate((el) => {
      el.scrollTop = 0;
      el.dispatchEvent(new Event("scroll"));
    });
    await page.evaluate(() => {
      window.chatMergeTest.messages(1);
      window.chatMergeTest.mode("merge_when_safe");
    });
    await check(timeline).toHaveJSProperty("scrollTop", 0);
    await check(timeline).toBeFocused();
    expect(await page.evaluate(() => window.chatMergeTest.calls())).toBe(0);
  } finally {
    await page.close();
  }
});
it("keeps the receipt readable when matching review evidence is unavailable", async () => {
  const { page } = await open();
  try {
    await receipt(page).getByRole("button", { name: "Open its review" }).waitFor();
    const commit = receipt(page).locator(".chat-merge-commit > span");
    const fullCommit = await commit.getAttribute("title");
    if (!fullCommit) throw new Error("Missing full merge commit");
    expect(await receipt(page).ariaSnapshot()).toContain(`Merged into main Commit ${fullCommit}`);
    await page.evaluate(() => {
      window.chatMergeTest.removeProposal();
    });
    await check(receipt(page).getByText("Its review is unavailable.")).toBeVisible();
    await check(receipt(page).getByRole("button")).toHaveCount(0);
  } finally {
    await page.close();
  }
});
it.each([
  { decided_by: "person" },
  { decided_by: "cross_review" },
  { decision: "rejected", merge: null },
  { decision: "changes_requested", merge: null },
  { merge: "conflict" },
] satisfies Partial<ChangeResolved>[])(
  "FocusPanel keeps generic rendering for %j",
  async (patch) => {
    const { page } = await open();
    try {
      await check(receipt(page)).toHaveCount(1);
      await page.evaluate((patch) => {
        window.chatMergeTest.resolve(patch);
      }, patch);
      const timeline = page.getByRole("list", { name: "Conversation timeline" });
      // The original rules receipt stays in sequence; only the new entry is generic.
      await check(receipt(page)).toHaveCount(1);
      await check(
        timeline.getByText(
          patch.decision === "changes_requested"
            ? "You requested changes"
            : patch.decision === "rejected"
              ? "You rejected the change"
              : "You approved the change",
          { exact: false },
        ),
      ).toHaveCount(1);
    } finally {
      await page.close();
    }
  },
);
it.each([1920, 1280])("separates the replay caption and chip at %spx", async (width) => {
  const { page, mutations } = await open({ replay: true, width });
  try {
    await check(chip(page)).toBeVisible();
    const gap = await page.locator(".conversation-composer").evaluate((el) => {
      const caption = el.querySelector("p"),
        actions = el.querySelector(".composer-actions");
      if (!caption || !actions) throw new Error("Missing replay caption or actions");
      return actions.getBoundingClientRect().top - caption.getBoundingClientRect().bottom;
    });
    expect(gap).toBe(8);
    expect(mutations).toEqual([]);
    if (process.env.CHAT_MERGE_SHOTS === "1") {
      await mkdir(`${shots}replay`, { recursive: true });
      await page.screenshot({
        path: `${shots}replay/${String(width)}-dark.png`,
        animations: "disabled",
      });
    }
  } finally {
    await page.close();
  }
});
const shots = fileURLToPath(new URL("../../../.shots/chat-merge/", import.meta.url));
it.each(
  [1920, 1280].flatMap((width) =>
    ["dark", "day"].flatMap((theme) =>
      ["empty", "merged"].map((state) => ({ width, theme, state })),
    ),
  ),
)("measures and captures $state-$width-$theme", async ({ width, theme, state }) => {
  const { page } = await open({ width, theme, merged: state === "merged" });
  try {
    await check(chip(page)).toBeVisible();
    if (state === "merged") await check(receipt(page)).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const measured = await page.evaluate(() => {
      const styles = (selector: string) => {
        const el = document.querySelector(selector);
        if (!el) return null;
        const s = getComputedStyle(el),
          r = el.getBoundingClientRect();
        return {
          x: r.x,
          width: r.width,
          height: r.height,
          radius: s.borderRadius,
          padding: s.padding,
          gap: s.gap,
          columns: s.gridTemplateColumns,
          font: s.fontSize,
          weight: s.fontWeight,
          lineHeight: s.lineHeight,
          margin: s.margin,
        };
      };
      return {
        chip: styles(".chat-merge-chip"),
        receipt: styles(".chat-merge-receipt"),
        title: styles(".chat-merge-title strong"),
        caption: styles(".chat-merge-title span"),
        reasons: styles(".chat-merge-reasons"),
        links: styles(".chat-merge-links"),
        thread: styles(".conversation-entries"),
        overflow:
          document.documentElement.scrollWidth > innerWidth ||
          [".shell-center", ".composer-actions", ".chat-merge-receipt"].some((selector) => {
            const el = document.querySelector(selector);
            return el ? el.scrollWidth > el.clientWidth : false;
          }),
      };
    });
    expect(measured.chip?.height).toBe(30);
    expect(measured.chip?.radius).toBe("7px");
    expect(measured.overflow).toBe(false);
    for (const el of await page
      .locator(".composer-actions button, .chat-empty button, .chat-merge-links button")
      .all()) {
      const r = await el.boundingBox();
      expect(r && r.x >= 0 && r.x + r.width <= width).toBe(true);
    }
    if (process.env.CHAT_MERGE_SHOTS === "1") {
      await mkdir(shots, { recursive: true });
      const name = `${state}-${String(width)}-${theme}`;
      await page.screenshot({ path: `${shots}${name}.png`, animations: "disabled" });
      await writeFile(`${shots}${name}.json`, JSON.stringify(measured, null, 2));
    }
  } finally {
    await page.close();
  }
});
