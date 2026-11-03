import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { ServerResponse, IncomingMessage } from "node:http";
import { createServer, type ViteDevServer, type Plugin } from "vite";
import { expect as check, type Browser } from "@playwright/test";
import { afterAll, beforeAll, expect } from "vitest";
import type { PreviewStatus, ServerEvent, StartPreview } from "@contracts";
import references from "../../../../fixtures/views/reference-cases.json";
import { launchBrowser } from "../../browserLaunch.support";
import { captureCheckpoint } from "./previewPanel.browser.support";

export const chatDirectory = fileURLToPath(
  new URL("../../../../.shots/preview-chat/", import.meta.url),
);
export const chatSizes = [
  { width: 1920, height: 1080 },
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
];
const leadId = "agent-900001";
const stamp = "2026-11-03T00:00:00.000Z";
export function chatEvents(): ServerEvent[] {
  const events = structuredClone(references.events.slice(0, 6)) as ServerEvent[];
  const agent = events.find((event) => event.type === "agent.status");
  if (agent?.type !== "agent.status") throw new Error("Missing fixture agent");
  events.push({ ...agent, seq: 7, payload: { ...agent.payload, state: "ready_for_review" } });
  const status = {
    agent_id: leadId,
    revision: 1,
    task: "Fixture: try the candidate shop",
    model: "fake",
    state: "working" as const,
    step: 0,
    detail: null,
    limits: null,
    requested_plan_id: null,
  };
  events.push(
    { type: "lead.status", seq: 8, ts: stamp, payload: status },
    {
      type: "lead.view",
      seq: 9,
      ts: stamp,
      payload: {
        agent_id: leadId,
        through_seq: 8,
        caption: null,
        view: { kind: "preview", agent_id: "agent-1" },
      },
    },
    {
      type: "lead.reply",
      seq: 10,
      ts: stamp,
      payload: {
        agent_id: leadId,
        answers_seq: null,
        woken_by: null,
        through_seq: 9,
        text: "The coupon change is ready to try. Check the shop, then review the change.",
        cut: null,
      },
    },
    { type: "lead.status", seq: 11, ts: stamp, payload: { ...status, revision: 2, state: "idle" } },
  );
  return events;
}
/** Test-only instrumentation, injected into source served by this fixture, never product files. */
function counters(events: ServerEvent[], replay: boolean): Plugin {
  return {
    name: "preview-chat-proof",
    enforce: "pre",
    transform(source, id) {
      if (id.endsWith("/transport/index.ts") && replay) {
        source = source.replace(
          'import.meta.env.MODE === "replay"',
          'new URLSearchParams(location.search).get("replay") === "true"',
        );
        source = source.replace(
          /export const replaySource = chooseReplaySource\([\s\S]*?\n\);/,
          `export const replaySource = chooseReplaySource(null, ${JSON.stringify(events.map((e) => JSON.stringify(e)).join("\n"))});`,
        );
      }
      const targets: [string, string, string][] = [
        ["/shell/ChatShell.tsx", "export function ChatShell(", "  return ("],
        [
          "/conversation/ConversationPanel.tsx",
          "export function ConversationPanel(",
          "  const { header",
        ],
        ["/graph/GraphView.tsx", "export function GraphView(", "  const "],
        ["/preview/PreviewPanel.tsx", "function PreviewPhase(", "  const loadedUrl"],
      ];
      for (const [path, start, anchor] of targets) {
        if (!id.endsWith(path)) continue;
        const begin = source.indexOf(start),
          at = source.indexOf(anchor, begin);
        if (begin < 0 || at < 0) throw new Error(`Missing render instrumentation anchor: ${path}`);
        const key = path.includes("ChatShell")
          ? "shell"
          : path.includes("Conversation")
            ? "conversation"
            : path.includes("Graph")
              ? "graph"
              : "phase";
        source =
          source.slice(0, at) +
          `  window.previewChatRenders ??= {}; window.previewChatRenders.${key} = (window.previewChatRenders.${key} ?? 0) + 1;\n` +
          source.slice(at);
      }
      if (id.endsWith("/transport/live.ts"))
        source = source.replace(
          "          retryWindow = 500;",
          '          if (event.type === "preview.status" && event.payload.state === "ready") { window.previewChatTiming ??= {}; window.previewChatTiming.ready ??= Date.now(); }\n          retryWindow = 500;',
        );
      if (id.endsWith("/preview/PreviewPanel.tsx"))
        source = source.replace(
          "progress.load(frame);",
          "window.previewChatTiming ??= {}; window.previewChatTiming.loaded ??= Date.now(); progress.load(frame);",
        );
      return source;
    },
  };
}
declare global {
  interface Window {
    previewChatRenders: Record<string, number>;
    previewChatTiming?: { clicked?: number; ready?: number; loaded?: number };
  }
}
export function previewChatSupport(real = false, enabled = true) {
  const support = new ChatSupport(real);
  beforeAll(() => (enabled ? support.start() : undefined), 60_000);
  afterAll(() => support.dispose());
  return support;
}
class ChatSupport {
  server?: ViteDevServer;
  browser?: Browser;
  url = "";
  html = "";
  events = chatEvents();
  streams = new Set<ServerResponse>();
  candidate?: StartPreview;
  constructor(readonly real: boolean) {}
  async start() {
    if (this.real) {
      const path = process.env.OVERSIGHT_PREVIEW_ACCEPTANCE_CANDIDATE;
      if (!path) throw new Error("Run tools.preview_chat_browser against real C");
      this.candidate = JSON.parse(await readFile(path, "utf8")) as StartPreview;
    }
    const server = await createServer({
      appType: "custom",
      root: fileURLToPath(new URL("../../../", import.meta.url)),
      cacheDir: `${chatDirectory}cache-${this.real ? "real" : "fixture"}`,
      optimizeDeps: { entries: ["src/app/preview/PreviewChat.browser.fixture.tsx"] },
      plugins: [counters(this.events, !this.real)],
      server: this.real
        ? {}
        : {
            port: 0,
            strictPort: false,
            headers: { "Content-Security-Policy": "frame-src http://localhost:19800" },
          },
    });
    this.server = server;
    // Install fixture SSE before Vite's API proxy. Real-local sessions use the production proxy.
    if (!this.real)
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
      '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root" style="height:100dvh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/preview/PreviewChat.browser.fixture.tsx";</script></body></html>',
    );
    server.middlewares.use((request, response, next) => {
      if (request.url?.split("?")[0] !== "/preview-chat") {
        next();
        return;
      }
      response.setHeader("Content-Type", "text/html");
      response.end(this.html);
    });
    await server.listen();
    this.url = server.resolvedUrls?.local[0] ?? "";
    this.browser = await launchBrowser();
    await mkdir(`${chatDirectory}${this.real ? "real-local" : "fixture"}`, { recursive: true });
  }
  append(event: ServerEvent) {
    const next = { ...event, seq: this.events.length + 1, ts: stamp };
    this.events.push(next);
    for (const stream of this.streams)
      stream.write(`id: ${String(next.seq)}\ndata: ${JSON.stringify(next)}\n\n`);
  }
  send(payload: PreviewStatus) {
    const event: ServerEvent = {
      seq: this.events.length + 1,
      ts: stamp,
      type: "preview.status",
      payload,
    };
    this.append(event);
  }
  async session(viewport = chatSizes[0], theme = "dark", extra = "") {
    if (!this.browser || !viewport) throw new Error("Missing Chat browser");
    if (!this.real)
      this.events = extra.includes("replyLate") ? chatEvents().slice(0, 9) : chatEvents();
    const context = await this.browser.newContext({ viewport, reducedMotion: "reduce" });
    context.setDefaultTimeout(7_000);
    const pageErrors: string[] = [],
      diagnostics: { type: string; text: string; expected: boolean }[] = [];
    const calls = { start: 0, renew: 0, stop: 0, details: 0, form: 0 };
    let serial = 0;
    let status: PreviewStatus | null = null;
    let started = 0,
      readyAt: number | null = null,
      loadedAt: number | null = null;
    let expectedError = false;
    const requests: StartPreview[] = [];
    const base = this.candidate ?? {
      run_id: "run-20261027-140312",
      agent_id: "agent-1",
      revision: 1,
      head_commit: "head",
    };
    if (!this.real) {
      await context.route("**/previews**", async (route) => {
        const request = route.request(),
          path = new URL(request.url()).pathname;
        let body: unknown;
        if (path === "/previews") {
          calls.start++;
          requests.push(request.postDataJSON() as StartPreview);
          status = {
            ...base,
            change_id: "change-1",
            preview_id: `preview-${(++serial).toString(16).padStart(32, "0")}`,
            state: "starting",
            reason: null,
          };
          body = { preview_id: status.preview_id, lease_token: "F".repeat(43) };
        } else if (path.endsWith("/stop")) {
          calls.stop++;
          if (status) {
            status = { ...status, state: "stopped", reason: "closed" };
            this.send(status);
          }
          body = status;
        } else if (path.endsWith("/lease")) {
          calls.renew++;
          body = status;
        } else {
          calls.details++;
          body = { status, url: status?.state === "ready" ? "http://localhost:19800/" : null };
        }
        await route.fulfill({
          status: path === "/previews" ? 202 : 200,
          headers: { "Cache-Control": "no-store" },
          json: body,
        });
      });
      await context.route("http://localhost:19800/**", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="font:16px sans-serif;margin:24px"><p>Fixture shop</p><h1>Candidate shop</h1><form><label>Quantity <input value="1" type="number" style="max-width:100%"></label><p><label>Coupon <input value="" style="max-width:100%"></label></p><button>Update cart</button></form><p>Total: <output data-total-cents="1500">1500 cents</output></p></body></html>',
        }),
      );
    }
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) =>
      diagnostics.push({
        type: message.type(),
        text: message.text(),
        expected: expectedError && message.type() === "error",
      }),
    );
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (request.method() === "POST" && path === "/previews") started = Date.now();
      if (this.real) {
        if (path.endsWith("/lease")) calls.renew++;
        if (path.endsWith("/stop")) calls.stop++;
        if (path.endsWith("/cart")) calls.form++;
      }
    });
    page.on("response", (response) => {
      if (response.url().includes("/previews/") && response.request().method() === "GET") {
        void response
          .json()
          .then((value: { status?: PreviewStatus }) => {
            if (value.status) status = value.status;
            if (value.status?.state === "ready" && readyAt === null) readyAt = Date.now();
          })
          .catch(() => {});
      }
    });
    page.on("framenavigated", (frame) => {
      if (frame.parentFrame() && frame.url() !== "about:blank") loadedAt = Date.now();
    });
    await page.goto(`${this.url}preview-chat?theme=${theme}&real=${String(this.real)}&${extra}`);
    await page.evaluate(() => document.fonts.ready);
    const lead = page.locator(".chat-lead-row");
    if (!extra.includes("panelFixture")) {
      await check(lead).toHaveCount(1);
      await lead.click();
      await check(page.getByRole("button", { name: "Preview app", exact: true })).toBeVisible();
    }
    const publish = (state: PreviewStatus["state"], reason: PreviewStatus["reason"] = null) => {
      if (!status) throw new Error("Click Preview app first");
      status = { ...status, state, reason };
      if (state === "ready") readyAt = Date.now();
      this.send(status);
    };
    return {
      page,
      context,
      calls,
      requests,
      pageErrors,
      diagnostics,
      viewport,
      theme,
      publish,
      reply: () => {
        const event = chatEvents()[9];
        if (event) this.append(event);
      },
      invalidate: () => {
        const event = chatEvents()[3];
        if (event) this.append(event);
      },
      expectedError: () => {
        expectedError = true;
      },
      identity: () => status,
      timing: () => ({
        timeToReadyMs: readyAt === null ? null : readyAt - started,
        timeToInteractiveMs: loadedAt === null ? null : loadedAt - started,
      }),
      close: async () => {
        await page
          .locator(".preview-panel button")
          .click({ timeout: 1000 })
          .catch(() => {});
        await context.close();
      },
    };
  }
  async capture(session: Awaited<ReturnType<ChatSupport["session"]>>, state: string) {
    const { page, viewport, theme, pageErrors, diagnostics } = session;
    const metrics = await page.evaluate(() => {
      const rect = (selector: string) =>
        (document.querySelector(selector)?.getBoundingClientRect().toJSON() as unknown) ??
        "inapplicable";
      const over = (node: Element | null) =>
        node && node.clientWidth ? Math.max(0, node.scrollWidth - node.clientWidth) : 0;
      return {
        rectangles: {
          conversation: rect(".conversation-panel"),
          panel: rect(".preview-panel"),
          iframe: rect("iframe"),
          composer: rect(".conversation-composer"),
          option: rect(".conversation-entries [role=listitem]:has(.lead-preview-option)"),
        },
        overflow: {
          document: over(document.documentElement),
          body: over(document.body),
          conversation: over(document.querySelector(".conversation-panel")),
          panel: over(document.querySelector(".preview-panel")),
        },
        fontReady: document.fonts.status,
        reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
        renders: window.previewChatRenders,
        timing: window.previewChatTiming,
      };
    });
    const frame = page.frames().find((frame) => frame.parentFrame());
    const appOverflow = frame
      ? await frame.evaluate(() =>
          Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
        )
      : "inapplicable";
    const name = `${state}-${String(viewport.width)}-${theme}`;
    const destination = `${chatDirectory}${this.real ? "real-local" : "fixture"}/${name}`;
    const checkpoint = await captureCheckpoint();
    await page.screenshot({ path: `${destination}.png` });
    const manifest = {
      png: `${name}.png`,
      state,
      viewport,
      theme,
      ...checkpoint,
      provenance: this.real
        ? "Real local C shop / scripted fake model / fixture-authored lead inputs / Linux Chromium"
        : "Fixture inputs and shop / Linux Chromium",
      candidate: session.identity() ?? this.candidate ?? session.requests.at(-1) ?? null,
      ...metrics,
      appOverflow,
      pageErrors: [...pageErrors],
      console: [...diagnostics],
      ...session.timing(),
      timeToReadyMs:
        metrics.timing?.clicked && metrics.timing.ready
          ? metrics.timing.ready - metrics.timing.clicked
          : null,
      timeToInteractiveMs:
        metrics.timing?.clicked && metrics.timing.loaded
          ? metrics.timing.loaded - metrics.timing.clicked
          : null,
    };
    await writeFile(`${destination}.json`, JSON.stringify(manifest, null, 2) + "\n");
    expect(pageErrors).toEqual([]);
    expect(diagnostics.filter((item) => item.type === "error" && !item.expected)).toEqual([]);
    expect(Object.values(metrics.overflow).every((value) => value === 0)).toBe(true);
    expect(appOverflow === "inapplicable" || appOverflow === 0).toBe(true);
    return manifest;
  }
  async dispose() {
    for (const stream of this.streams) stream.end();
    await this.browser?.close();
    await this.server?.close();
  }
}
