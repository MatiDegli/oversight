import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { IncomingMessage, ServerResponse } from "node:http";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import type { PreviewStatus, ServerEvent, StartPreview } from "@contracts";
import { launchBrowser } from "../../browserLaunch.support";
import { chatEvents, chatSizes } from "./previewChat.browser.support";
import {
  carrierText,
  fixtureTexts,
  hostileFixture,
  hostileText,
} from "./previewStartup.browser.support";

export { chatSizes };
export const shotsDirectory = fileURLToPath(
  new URL("../../../../.shots/preview-startup-chat/", import.meta.url),
);
export const hostileArguments = [
  [hostileText, "two words", '"quoted"', "C:\\app\\server.js", "&\u2028\u2029"],
];
export const chatHostileFixture = { ...hostileFixture, argv: hostileArguments };
const texts = {
  valid: fixtureTexts.valid,
  missing: fixtureTexts.missing,
  invalid: fixtureTexts.invalid,
  hostile: carrierText(chatHostileFixture),
  "hostile-invalid": carrierText({ ...chatHostileFixture, version: 2 } as never),
};
export type Scenario = keyof typeof texts;

/** Fixture indexes enter the real App before mount; no reader or product source replacement. */
export class StartupChatBrowser {
  private server?: ViteDevServer;
  private browser?: Browser;
  private html = "";
  private url = "";
  private events = chatEvents();
  private streams = new Set<ServerResponse>();

  async start() {
    const server = await createServer({
      appType: "custom",
      root: fileURLToPath(new URL("../../../", import.meta.url)),
      cacheDir: `${shotsDirectory}cache`,
      optimizeDeps: { entries: ["src/app/preview/PreviewChat.browser.fixture.tsx"] },
      server: {
        host: "127.0.0.1",
        port: 0,
        strictPort: false,
        headers: { "Content-Security-Policy": "frame-src http://127.0.0.1:19800" },
      },
    });
    this.server = server;
    server.middlewares.stack.unshift({
      route: "",
      handle: (request: IncomingMessage, response: ServerResponse, next: () => void) => {
        if (!request.url?.startsWith("/events?")) {
          next();
          return;
        }
        response.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-store",
        });
        this.streams.add(response);
        const after = Number(new URL(request.url, "http://fixture").searchParams.get("after"));
        for (const event of this.events.filter((event) => event.seq > after))
          response.write(`id: ${String(event.seq)}\ndata: ${JSON.stringify(event)}\n\n`);
        request.on("close", () => this.streams.delete(response));
      },
    });
    this.html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><!--preview-startup-carrier--><div id="root" style="height:100dvh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/preview/PreviewChat.browser.fixture.tsx";</script></body></html>',
    );
    await server.listen();
    this.url = server.resolvedUrls?.local[0] ?? "";
    if (!this.url) throw new Error("Missing fixture server URL");
    this.browser = await launchBrowser();
  }

  private append(event: ServerEvent) {
    const next = { ...event, seq: this.events.length + 1 };
    this.events.push(next);
    for (const stream of this.streams)
      stream.write(`id: ${String(next.seq)}\ndata: ${JSON.stringify(next)}\n\n`);
  }

  async open(scenario: Scenario, viewport = chatSizes[0], theme = "dark") {
    if (!this.browser || !viewport) throw new Error("Missing fixture browser");
    this.events = chatEvents();
    const context = await this.browser.newContext({ viewport, reducedMotion: "reduce" });
    context.setDefaultTimeout(7_000);
    const page = await context.newPage();
    const pageErrors: string[] = [],
      unexpectedRequests: string[] = [],
      navigations: string[] = [],
      dialogs: string[] = [];
    const requests: StartPreview[] = [];
    let status: PreviewStatus | null = null;
    // All observation begins before the navigation response and App mount.
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("request", (request) => {
      if (/\/preview-(injection|navigation)/u.test(request.url()))
        unexpectedRequests.push(request.url());
    });
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame()) navigations.push(frame.url());
    });
    page.on("dialog", async (dialog) => {
      dialogs.push(dialog.message());
      await dialog.dismiss();
    });
    await context.route("**/previews**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/previews") {
        const input = route.request().postDataJSON() as StartPreview;
        requests.push(input);
        status = {
          ...input,
          change_id: "change-1",
          preview_id: `preview-${"a".repeat(32)}`,
          state: "starting",
          reason: null,
        };
        await route.fulfill({
          status: 202,
          headers: { "Cache-Control": "no-store" },
          json: { preview_id: status.preview_id, lease_token: "F".repeat(43) },
        });
      } else if (path.endsWith("/stop")) {
        if (status) status = { ...status, state: "stopped", reason: "closed" };
        await route.fulfill({ headers: { "Cache-Control": "no-store" }, json: status });
      } else if (path.endsWith("/lease"))
        await route.fulfill({ headers: { "Cache-Control": "no-store" }, json: status });
      else
        await route.fulfill({
          headers: { "Cache-Control": "no-store" },
          json: { status, url: status?.state === "ready" ? "http://127.0.0.1:19800/" : null },
        });
    });
    await context.route("http://127.0.0.1:19800/**", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: "<html><body><h1>Fixture candidate shop</h1></body></html>",
      }),
    );
    const text = texts[scenario];
    const carrier =
      text === undefined
        ? ""
        : `<script type="application/json" id="oversight-preview-startup">${text}</script>`;
    await page.route("**/preview-startup-chat?*", (route) =>
      route.fulfill({
        contentType: "text/html; charset=utf-8",
        body: this.html.replace("<!--preview-startup-carrier-->", carrier),
      }),
    );
    const response = await page.goto(`${this.url}preview-startup-chat?theme=${theme}`);
    const initialIndex = await response?.text();
    await page.evaluate(() => document.fonts.ready);
    const workspace = page.getByRole("group", { name: "Workspace view" });
    await workspace.getByRole("button", { name: "Oversight", exact: true }).click();
    await workspace.getByRole("button", { name: "Chat", exact: true }).click();
    await page.locator('.chat-agent-row[data-agent-origin="agent-1"]').click();
    await check(
      page.getByRole("region", { name: "Agent A conversation", exact: true }),
    ).toBeVisible();
    await page.locator(".conversation-composer").waitFor({ state: "visible" });
    return {
      page,
      context,
      scenario,
      viewport,
      theme,
      pageErrors,
      unexpectedRequests,
      navigations,
      dialogs,
      requests,
      initialIndex,
      carrier,
      lead: async () => {
        await page.locator(".chat-lead-row").click();
        await check(page.getByRole("button", { name: "Preview app", exact: true })).toBeVisible();
      },
      arrive: () => {
        const event = chatEvents()[9];
        if (!event) throw new Error("Missing reply fixture");
        this.append(event);
      },
      ready: () => {
        if (!status) throw new Error("Preview was not activated");
        status = { ...status, state: "ready" };
        this.append({
          type: "preview.status",
          seq: 0,
          ts: "2026-11-03T00:00:01Z",
          payload: status,
        });
      },
      close: () => context.close(),
    };
  }

  async close() {
    for (const stream of this.streams) stream.end();
    await this.browser?.close();
    await this.server?.close();
  }
}
export type Session = Awaited<ReturnType<StartupChatBrowser["open"]>>;

export async function capture(session: Session) {
  const { page, scenario, viewport, theme } = session;
  // At phone width Chat already scrolls as a whole; position evidence by a person-equivalent scroll.
  if (viewport.width === 390) await page.locator(".conversation-composer").scrollIntoViewIfNeeded();
  await page.evaluate(
    ({ scenario }) => {
      const label = document.createElement("aside");
      label.textContent = `Fixture · Chat preview startup · ${scenario}`;
      label.style.cssText =
        "position:fixed;bottom:28px;right:8px;z-index:10000;padding:4px 8px;background:var(--page);color:var(--muted);font-size:12px";
      label.setAttribute("aria-label", "Fixture evidence");
      document.body.append(label);
    },
    { scenario },
  );
  const horizontalOverflow = await page.evaluate(() => {
    const nodes = [
      document.documentElement,
      document.body,
      ...document.querySelectorAll(
        ".conversation-panel, .conversation-composer, .preview-startup, .preview-startup dd, .preview-startup code",
      ),
    ];
    return Math.max(
      0,
      ...nodes
        .filter((node) => node.clientWidth > 0)
        .map((node) => node.scrollWidth - node.clientWidth),
    );
  });
  await mkdir(shotsDirectory, { recursive: true });
  const stem = `${scenario}-${String(viewport.width)}x${String(viewport.height)}-${theme}-fixture`;
  await page.screenshot({ path: `${shotsDirectory}${stem}.png` });
  await writeFile(
    `${shotsDirectory}${stem}.json`,
    JSON.stringify(
      {
        evidence: "fixture",
        presentation:
          viewport.width === 390
            ? "selected coding agent; manual scroll to composer"
            : "selected coding agent",
        scenario,
        viewport,
        theme,
        pageErrors: session.pageErrors,
        horizontalOverflow,
      },
      null,
      2,
    ) + "\n",
  );
  return horizontalOverflow;
}
