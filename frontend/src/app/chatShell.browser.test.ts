import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type { GraphProbeHost } from "../graph";
import type { chatShellTest } from "./chatShell.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
let streams = 0;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/chat-shell-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
    plugins: [
      {
        name: "idle-fixture-stream",
        configureServer(vite) {
          vite.middlewares.use((req, res, next) => {
            if (!req.url?.startsWith("/events?")) {
              next();
              return;
            }
            streams++;
            res.setHeader("Content-Type", "text/event-stream");
            res.write(": connected\n\n");
          });
        },
      },
    ],
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/chatShell.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
const viewButton = (page: Page, name: string) =>
  page.getByRole("group", { name: "Workspace view" }).getByRole("button", { name, exact: true });
const graph = (page: Page) => page.getByLabel("Repository graph", { exact: true });
const message = (page: Page) => page.getByRole("textbox", { name: "Message Agent A" });
async function open(
  options: Parameters<typeof chatShellTest.mount>[0] = {},
  width = 1920,
  theme = "dark",
) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : 800 },
    reducedMotion: "reduce",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.chatShellTest);
  await page.evaluate(
    ({ options, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.chatShellTest.mount(options);
    },
    { options, theme },
  );
  await page.evaluate(() => document.fonts.ready);
  return page;
}
async function drawn(page: Page) {
  await page.waitForFunction(
    () =>
      !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
  );
}
it("keeps the default, selected tab, draft, commands, graph identity and camera", async () => {
  const page = await open();
  try {
    await drawn(page);
    await check(viewButton(page, "Oversight")).toHaveAttribute("aria-pressed", "true");
    await message(page).fill("Keep this draft");
    const textarea = await message(page).elementHandle();
    const host = await graph(page).elementHandle();
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await page.waitForFunction(() => window.chatShellTest.contexts() > 0);
    const contexts = await page.evaluate(() => window.chatShellTest.contexts());
    const canvases = await graph(page).locator("canvas").elementHandles();
    const camera = await graph(page).evaluate((el: GraphProbeHost) => ({
      zoom: el.oversightGraph?.zoom(),
      pan: el.oversightGraph?.pan(),
    }));
    await viewButton(page, "Chat").click();
    await check(viewButton(page, "Chat")).toBeFocused();
    await check(page.locator(".oversight-stage")).toHaveAttribute("inert", "");
    await check(graph(page)).toBeHidden();
    await check(page.locator(".chat-agents")).toHaveAttribute("data-selected-agent", "agent-1");
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    expect(await page.evaluate(() => window.chatShellTest.calls())).toEqual([
      { command: "pause", id: "agent-1" },
    ]);
    await viewButton(page, "Oversight").focus();
    await page.keyboard.press("Enter");
    await check(viewButton(page, "Oversight")).toBeFocused();
    await check(page.getByRole("tab", { name: /Agent A · conversation/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await check(message(page)).toHaveValue("Keep this draft");
    expect(
      await textarea.evaluate(
        (el) => el === document.querySelector('[aria-label="Message Agent A"]'),
      ),
    ).toBe(true);
    expect(
      await host.evaluate((el) => el === document.querySelector('[aria-label="Repository graph"]')),
    ).toBe(true);
    expect(
      await graph(page).evaluate((el: GraphProbeHost) => ({
        zoom: el.oversightGraph?.zoom(),
        pan: el.oversightGraph?.pan(),
      })),
    ).toEqual(camera);
    expect(await page.evaluate(() => window.chatShellTest.contexts())).toBe(contexts);
    expect(await graph(page).locator("canvas").count()).toBe(canvases.length);
    for (const canvas of canvases) expect(await canvas.evaluate((el) => el.isConnected)).toBe(true);
    await viewButton(page, "Chat").click();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    expect(await page.evaluate(() => window.chatShellTest.calls())).toContainEqual({
      command: "message",
      id: "agent-1",
      text: "Keep this draft",
    });
    await check(message(page)).toHaveValue("");
    expect(await page.evaluate(() => window.chatShellTest.view().activeTab)).toBe(
      "conversation:agent-1",
    );
  } finally {
    await page.close();
  }
});
it("Chat-first requests no graph or field implementation until explicit entry", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const requested: string[] = [];
  page.on("request", (req) => requested.push(new URL(req.url()).pathname));
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  try {
    await page.goto(url);
    await page.waitForFunction(() => !!window.chatShellTest);
    await page.evaluate(() => {
      window.chatShellTest.mount({ initial: "Chat" });
    });
    await check(message(page)).toBeVisible();
    const implementation = () =>
      requested.filter((path) =>
        /GraphView|DotField|cytoscape|\/field\/controller|OversightStage/.test(path),
      );
    expect(implementation()).toEqual([]);
    expect(await page.evaluate(() => window.chatShellTest.contexts())).toBe(0);
    await check(graph(page)).toHaveCount(0);
    await viewButton(page, "Oversight").click();
    await drawn(page);
    expect(implementation().some((path) => path.includes("GraphView"))).toBe(true);
    const once = implementation();
    await viewButton(page, "Chat").click();
    await viewButton(page, "Oversight").click();
    expect(implementation()).toEqual(once);
    await check(graph(page)).toHaveCount(1);
  } finally {
    await page.close();
  }
});
it.each(["late", "failed"])(
  "keeps the switch and focus available with a %s stage",
  async (outcome) => {
    const page = await open({ initial: "Chat" });
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/OversightStage.tsx*", async (route) => {
      await gate;
      if (outcome === "failed") await route.abort("failed");
      else await route.continue();
    });
    try {
      await viewButton(page, "Oversight").click();
      await check(page.getByText("Loading repository graph…", { exact: true })).toBeVisible();
      await viewButton(page, "Chat").click();
      const search = page.getByRole("button", {
        name: "Find a function, file or command",
        exact: true,
      });
      await search.focus();
      release();
      if (outcome === "late") {
        await page.waitForFunction(
          () => !!document.querySelector('[aria-label="Repository graph"]'),
        );
      } else {
        await check(page.locator(".oversight-stage [role=alert]")).toHaveText(
          "This panel could not load. Reload the page to try again.",
        );
      }
      await check(search).toBeFocused();
      await check(viewButton(page, "Chat")).toHaveAttribute("aria-pressed", "true");
      await viewButton(page, "Oversight").click();
      if (outcome === "late") await drawn(page);
      else await check(page.getByRole("alert")).toBeVisible();
      await viewButton(page, "Chat").click();
      await check(message(page)).toBeVisible();
    } finally {
      release();
      await page.close();
    }
  },
);
it("real App retains one session while events apply and a command targets the same agent", async () => {
  const page = await open({ app: true }, 1280);
  try {
    await drawn(page);
    const first = streams;
    for (const control of await page.locator(".shell-top-bar button").all()) {
      const box = await control.boundingBox();
      expect(box && box.x >= 0 && box.x + box.width <= 1280).toBe(true);
    }
    await check(
      page.locator(".shell-top-bar").getByText("demo-repo", { exact: true }),
    ).toBeVisible();
    const navigations: string[] = [],
      commands: string[] = [];
    page.on("framenavigated", (frame) => navigations.push(frame.url()));
    await page.route("**/agents/agent-1/message", (route) => {
      commands.push(route.request().postData() ?? "");
      return route.fulfill({ status: 202 });
    });
    await page
      .getByRole("article", { name: "Agent A", exact: true })
      .getByRole("button", { name: "Conversation", exact: true })
      .click();
    await message(page).fill("Same session");
    await viewButton(page, "Chat").click();
    const focus = viewButton(page, "Chat");
    await page.evaluate(() => {
      window.chatShellTest.advance(36);
    });
    await check(focus).toBeFocused();
    await check(page.locator("#root")).toHaveAttribute("data-seq", "36");
    await viewButton(page, "Oversight").click();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await check(message(page)).toHaveValue("");
    expect(commands).toEqual([JSON.stringify({ text: "Same session" })]);
    expect(streams).toBe(first);
    expect(navigations).toEqual([]);
    expect(await page.evaluate(() => window.chatShellTest.seq())).toBe(36);
  } finally {
    await page.close();
  }
});
it("retains actual replay capabilities and source wording", async () => {
  const page = await open({ initial: "Chat", commands: false });
  try {
    await check(
      page.getByText("Recorded run: messages, pause and stop are off.", { exact: true }),
    ).toBeVisible();
    await check(page.getByRole("textbox")).toHaveCount(0);
    await check(page.getByRole("button", { name: /^(Send|Pause|Stop and discard)$/ })).toHaveCount(
      0,
    );
    await check(page.getByLabel("Status line")).toContainText("Recorded run · Oct 27, 2026");
  } finally {
    await page.close();
  }
});

it("conversation completion never focuses the Composer", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/ConversationPanel.tsx*", async (route) => {
    await gate;
    await route.continue();
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  try {
    await page.goto(url);
    await page.waitForFunction(() => !!window.chatShellTest);
    await page.evaluate(() => {
      window.chatShellTest.mount({ initial: "Chat" });
    });
    await viewButton(page, "Chat").focus();
    release();
    await check(message(page)).toBeVisible();
    await check(viewButton(page, "Chat")).toBeFocused();
  } finally {
    release();
    await page.close();
  }
});

const shots = fileURLToPath(new URL("../../../.shots/chat-shell/", import.meta.url));
it.each(
  [1920, 1280].flatMap((width) =>
    ["dark", "day"].flatMap((theme) =>
      ["empty", "working"].map((state) => ({ width, theme, state })),
    ),
  ),
)("measures and optionally captures $state-$width-$theme", async ({ width, theme, state }) => {
  const page = await open({ initial: "Chat", working: state === "working" }, width, theme);
  try {
    if (state === "working") await check(message(page)).toBeVisible();
    else await check(page.getByRole("textbox", { name: "Task for the new agent" })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const measured = await page.evaluate(() => {
      const box = (selector: string) => {
        const el = document.querySelector(selector);
        if (!el) return null;
        const { x, y, width, height } = el.getBoundingClientRect();
        return { x, y, width, height };
      };
      const entries = document.querySelector('.conversation-entries [role="listitem"] > div');
      return {
        columns: [".left-rail", ".chat-agents", ".shell-center", ".chat-work"].map(
          (s) => box(s)?.width,
        ),
        cap: box(".conversation-entries")?.width,
        gutter: entries ? parseFloat(getComputedStyle(entries).paddingLeft) : null,
        switch: box(".view-switch")?.height,
        textarea: box(".conversation-composer textarea"),
        actions: box(".composer-actions"),
        empty: box(".chat-empty > div")?.width,
        sideHeaders: [box(".chat-agents > header")?.height, box(".chat-work > header")?.height],
        status: box(".status-line")?.height,
      };
    });
    expect(measured.columns).toEqual(width === 1920 ? [56, 280, 1284, 300] : [56, 260, 704, 260]);
    expect(measured.switch).toBe(34);
    expect(measured.sideHeaders).toEqual([56, 56]);
    expect(measured.status).toBe(24);
    await check(page.locator(".chat-side-body > *")).toHaveCount(0);
    await check(page.locator(".chat-shell article, .chat-shell .file-agent-mark")).toHaveCount(0);
    await check(
      page.getByText(/^(Proposed change|Merged by rules|See on graph|New task)$/),
    ).toHaveCount(0);
    if (state === "working") {
      expect(measured.cap).toBe(width === 1920 ? 880 : 704);
      expect(measured.gutter).toBe(width === 1920 ? 24 : 20);
      expect(measured.textarea?.height).toBe(width === 1920 ? 64 : 46);
      expect(measured.textarea?.width).toBe(width === 1920 ? 832 : 664);
      expect(measured.actions?.height).toBe(width === 1920 ? 38 : 34);
      for (const button of await page.locator(".composer-actions button").all()) {
        const bounds = await button.boundingBox();
        expect(bounds && bounds.y + bounds.height <= (width === 1920 ? 1056 : 776)).toBe(true);
      }
    } else expect(measured.empty).toBe(width === 1920 ? 780 : 656);
    if (process.env.CHAT_SHELL_SHOTS === "1") {
      await mkdir(shots, { recursive: true });
      const name = `${state}-${String(width)}-${theme}`;
      await page.screenshot({ path: `${shots}${name}.png`, animations: "disabled" });
      await writeFile(`${shots}${name}.json`, JSON.stringify(measured, null, 2));
    }
    if (state === "working") {
      await page.evaluate(() => {
        window.chatShellTest.messages(40);
      });
      const timeline = page.getByRole("list", { name: "Conversation timeline" });
      expect(await timeline.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
      await timeline.focus();
      await timeline.evaluate((el) => {
        el.scrollTop = 0;
        el.dispatchEvent(new Event("scroll", { bubbles: true }));
      });
      await page.evaluate(() => {
        window.chatShellTest.messages(1);
      });
      await check(timeline).toBeFocused();
      await check(timeline).toHaveJSProperty("scrollTop", 0);
      await check(page.getByRole("button", { name: "Send", exact: true })).toBeVisible();
    }
  } finally {
    await page.close();
  }
});
