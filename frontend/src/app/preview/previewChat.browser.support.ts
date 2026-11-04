import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { request as httpRequest, type ServerResponse, type IncomingMessage } from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { createServer, type ViteDevServer, type Plugin } from "vite";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { afterAll, beforeAll, expect } from "vitest";
import type { ReplayPlayer } from "../../transport";
import type { State } from "../../state";
import { createHash } from "node:crypto";
import type { PreviewStatus, ServerEvent, StartPreview } from "@contracts";
import references from "../../../../fixtures/views/reference-cases.json";
import { launchBrowser } from "../../browserLaunch.support";
import { captureCheckpoint } from "./previewPanel.browser.support";
import { deferred } from "./previewFake";

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
      const replace = (anchor: string | RegExp, replacement: string) => {
        const count =
          typeof anchor === "string"
            ? source.split(anchor).length - 1
            : [...source.matchAll(new RegExp(anchor.source, "g"))].length;
        if (count !== 1)
          throw new Error(
            `Expected one proof instrumentation anchor in ${id}: ${String(anchor)}; found ${String(count)}`,
          );
        source = source.replace(anchor, () => replacement);
      };
      if (id.endsWith("/transport/index.ts") && replay) {
        replace(
          'import.meta.env.MODE === "replay"',
          'new URLSearchParams(location.search).get("replay") === "true"',
        );
        replace(
          /export const replaySource = chooseReplaySource\([\s\S]*?\n\);/,
          `export const replaySource = new URLSearchParams(location.search).has("publicRecording") ? chooseReplaySource(recorded.text, "", recorded.provenance) : chooseReplaySource(null, window.coldStartInput ?? ${JSON.stringify(events.map((e) => JSON.stringify(e)).join("\n"))});`,
        );
      }
      if (id.endsWith("/transport/index.ts"))
        replace("onReplay?.(player);", "window.coldStartPlayer = player; onReplay?.(player);");
      if (id.endsWith("/state/store.ts"))
        replace(
          "export const stateStore = createStateStore();",
          `export const stateStore = createStateStore();
window.coldStartTrace = { cursors: [], state: stateStore.getState().state };
stateStore.subscribe(({ state }) => { window.coldStartTrace.cursors.push(state.lastSeq); window.coldStartTrace.state = state; });
`,
        );
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
      if (id.endsWith("/transport/live.ts")) {
        replace(
          "      onError?.(error);",
          "      (window.coldStartFailures ??= []).push({ message: String(error), stack: error instanceof Error ? error.stack : undefined }); onError?.(error);",
        );
        replace(
          "          retryWindow = 500;",
          '          if (event.type === "preview.status" && event.payload.state === "ready") { window.previewChatTiming ??= {}; window.previewChatTiming.ready ??= performance.now(); }\n          retryWindow = 500;',
        );
      }
      if (id.endsWith("/preview/PreviewPanel.tsx"))
        replace(
          "progress.load(frame);",
          "window.previewChatTiming ??= {}; window.previewChatTiming.loaded ??= performance.now(); progress.load(frame);",
        );
      return source;
    },
  };
}
declare global {
  interface Window {
    coldStartAlerts?: string[];
    coldStartFailures?: { message: string; stack?: string }[];
    coldStartInput?: string;
    coldStartPlayer?: ReplayPlayer;
    coldStartTrace: { cursors: number[]; state: State };
    previewChatRenders: Record<string, number>;
    previewChatTiming?: { clicked?: number; ready?: number; loaded?: number };
  }
}
/** Opt-in lifecycle proof uses one HTTP authority across reloads and sibling pages. */
export interface ChatLifecycleHooks {
  previews: (
    request: IncomingMessage,
    response: ServerResponse,
    send: (status: PreviewStatus) => void,
  ) => void;
  beforeNavigate: (page: Page) => Promise<void>;
}
export function previewChatSupport(real = false, enabled = true, lifecycle?: ChatLifecycleHooks) {
  const support = new ChatSupport(real, lifecycle);
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
  api?: string;
  deliveries: {
    url: string;
    after: number;
    lastEventId: string | undefined;
    seqs: number[];
    bytes: number;
    chunks?: number[];
  }[] = [];
  candidate?: StartPreview;
  constructor(
    readonly real: boolean,
    readonly lifecycle?: ChatLifecycleHooks,
  ) {}
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
            headers: { "Content-Security-Policy": "frame-src http://127.0.0.1:19800" },
          },
    });
    this.server = server;
    if (this.lifecycle)
      server.middlewares.stack.unshift({
        route: "",
        handle: (request: IncomingMessage, response: ServerResponse, next: () => void) => {
          if (request.url?.startsWith("/previews"))
            this.lifecycle?.previews(request, response, (status) => {
              this.send(status);
            });
          else next();
        },
      });
    // Install fixture SSE before Vite's API proxy. Real-local sessions use the production proxy.
    if (!this.real)
      server.middlewares.stack.unshift({
        route: "",
        handle: (request: IncomingMessage, response: ServerResponse, next: () => void) => {
          if (!request.url?.startsWith("/events?")) {
            next();
            return;
          }
          if (this.api) {
            const delivery = {
              url: request.url,
              after: Number(new URL(request.url, "http://fixture").searchParams.get("after")),
              lastEventId: request.headers["last-event-id"] as string | undefined,
              seqs: [] as number[],
              bytes: 0,
              chunks: [] as number[],
            };
            this.deliveries.push(delivery);
            const proxy = httpRequest(
              `${this.api}${request.url}`,
              { headers: { ...request.headers, host: new URL(this.api).host } },
              (upstream) => {
                response.writeHead(upstream.statusCode ?? 500, upstream.headers);
                this.streams.add(response);
                let pending = "";
                upstream.setEncoding("utf8");
                upstream.on("data", (chunk: string) => {
                  delivery.bytes += Buffer.byteLength(chunk);
                  pending += chunk;
                  const frames = pending.split(/\r?\n\r?\n/);
                  pending = frames.pop() ?? "";
                  let count = 0;
                  for (const frame of frames) {
                    const data = frame.split(/\r?\n/).find((line) => line.startsWith("data: "));
                    if (data) {
                      delivery.seqs.push((JSON.parse(data.slice(6)) as ServerEvent).seq);
                      count++;
                    }
                  }
                  delivery.chunks.push(count);
                });
                upstream.pipe(response);
              },
            );
            proxy.on("error", () => response.destroy());
            request.on("close", () => {
              proxy.destroy();
              this.streams.delete(response);
            });
            proxy.end();
            return;
          }
          response.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
          });
          this.streams.add(response);
          const after = Number(new URL(request.url, "http://fixture").searchParams.get("after"));
          const events = this.events.filter((event) => event.seq > after);
          const body = events
            .map((event) => `id: ${String(event.seq)}\ndata: ${JSON.stringify(event)}\n\n`)
            .join("");
          this.deliveries.push({
            url: request.url,
            after,
            lastEventId: request.headers["last-event-id"] as string | undefined,
            seqs: events.map((event) => event.seq),
            bytes: Buffer.byteLength(body),
          });
          response.write(body);
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
  async session(viewport = chatSizes[0], theme = "dark", extra = "", input?: ServerEvent[]) {
    if (!this.browser || !viewport) throw new Error("Missing Chat browser");
    if (!this.real && !this.lifecycle)
      this.events =
        input ?? (extra.includes("replyLate") ? chatEvents().slice(0, 9) : chatEvents());
    this.deliveries = [];
    const context = await this.browser.newContext({ viewport, reducedMotion: "reduce" });
    context.setDefaultTimeout(7_000);
    if (input)
      await context.addInitScript(() => {
        window.coldStartAlerts = [];
        new MutationObserver(() => {
          for (const node of document.querySelectorAll(
            '[role="alert"], [aria-label="Status line"]',
          )) {
            const text = node.textContent;
            if (
              /Maximum update depth|Connection lost|Transport failed/i.test(text) &&
              !window.coldStartAlerts?.includes(text)
            )
              window.coldStartAlerts?.push(text);
          }
        }).observe(document, { childList: true, subtree: true, characterData: true });
      });
    if (input)
      await context.addInitScript(
        (text) => {
          window.coldStartInput = text;
        },
        input.map((event) => JSON.stringify(event)).join("\n"),
      );
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
    const initialDetails = deferred<undefined>();
    const initialDetailsObserved = deferred<undefined>();
    const base = this.candidate ?? {
      run_id: "run-20261027-140312",
      agent_id: "agent-1",
      revision: 1,
      head_commit: "head",
    };
    if (!this.real) {
      if (!this.lifecycle)
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
            if (extra.includes("lateInitialDetails") && calls.details > 1)
              await initialDetailsObserved.promise;
            body = { status, url: status?.state === "ready" ? "http://127.0.0.1:19800/" : null };
            if (extra.includes("lateInitialDetails") && calls.details === 1)
              await initialDetails.promise;
          }
          await route.fulfill({
            status: path === "/previews" ? 202 : 200,
            headers: { "Cache-Control": "no-store" },
            json: body,
          });
        });
      await context.route("http://127.0.0.1:19800/**", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="font:16px sans-serif;margin:24px"><p>Fixture shop</p><h1>Candidate shop</h1><form><label>Quantity <input value="1" type="number" style="max-width:100%"></label><p><label>Coupon <input value="" style="max-width:100%"></label></p><button>Update cart</button></form><p>Total: <output data-total-cents="1500">1500 cents</output></p></body></html>',
        }),
      );
    }
    const page = await context.newPage();
    if (input && extra.includes("replay=true")) await page.clock.install();
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
            // Fixture handlers own fake status; a delayed HTTP read can describe an older state.
            if (this.real && value.status) status = value.status;
            if (value.status?.state === "starting") initialDetailsObserved.resolve(undefined);
            if (value.status?.state === "ready" && readyAt === null) readyAt = Date.now();
          })
          .catch(() => {});
      }
    });
    page.on("framenavigated", (frame) => {
      if (frame.parentFrame() && frame.url() !== "about:blank") loadedAt = Date.now();
    });
    await this.lifecycle?.beforeNavigate(page);
    await page.goto(`${this.url}preview-chat?theme=${theme}&real=${String(this.real)}&${extra}`, {
      timeout: 30_000,
    });
    await page.evaluate(() => document.fonts.ready);
    const lead = page.locator(".chat-lead-row");
    if (!extra.includes("panelFixture") && !input) {
      await check(lead).toHaveCount(1);
      await lead.click();
      await check(page.getByRole("button", { name: "Preview app", exact: true })).toBeVisible();
    }
    const publish = (state: PreviewStatus["state"], reason: PreviewStatus["reason"] = null) => {
      if (!status) throw new Error("Click Preview app first");
      status = { ...status, state, reason };
      if (state === "ready") readyAt = Date.now();
      this.send(status);
      initialDetails.resolve(undefined);
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
        initialDetails.resolve(undefined);
        initialDetailsObserved.resolve(undefined);
        if (await page.locator(".preview-panel button").count())
          await page.locator(".preview-panel button").click({ timeout: 1000 });
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
        cursor: document.getElementById("root")?.dataset.seq,
        applicationErrors: window.coldStartFailures ?? [],
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

export interface ColdStartSource {
  path: string;
  bytes: number;
  sha256: string;
  events: ServerEvent[];
  beforeLeadView: number | "no lead.view";
  complete: boolean;
  provenance: unknown;
}
/** Inventory every event-bearing JSON/JSONL array, including non-stream contract examples. */
export async function coldStartSources(): Promise<ColdStartSource[]> {
  const root = new URL("../../../../fixtures/", import.meta.url);
  const sources: ColdStartSource[] = [];
  for (const path of (await readdir(root, { recursive: true })).sort()) {
    if (!/\.jsonl?$/.test(path)) continue;
    const raw = await readFile(new URL(path, root), "utf8");
    const value: unknown = path.endsWith(".jsonl")
      ? raw
          .trim()
          .split(/\r?\n/)
          .map((line) => JSON.parse(line) as unknown)
      : JSON.parse(raw);
    let provenance: unknown = null;
    try {
      provenance = JSON.parse(await readFile(new URL(`${path}.provenance.json`, root), "utf8"));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    const visit = (value: unknown, key: string) => {
      if (
        Array.isArray(value) &&
        value.length &&
        value.every(
          (item: unknown) =>
            typeof item === "object" &&
            item !== null &&
            ["seq", "ts", "type", "payload"].every((key) => key in item),
        )
      ) {
        const events = value as ServerEvent[];
        const first = events.findIndex((event) => event.type === "lead.view");
        sources.push({
          path: `fixtures/${path}${key}`,
          bytes: Buffer.byteLength(raw),
          sha256: createHash("sha256").update(raw).digest("hex"),
          events,
          beforeLeadView: first < 0 ? "no lead.view" : first,
          complete:
            events[0]?.type === "run.started" && events.every((event, i) => event.seq === i + 1),
          provenance,
        });
      } else if (value && typeof value === "object" && !Array.isArray(value)) {
        for (const [name, child] of Object.entries(value)) visit(child, `${key}#${name}`);
      }
    };
    visit(value, "");
  }
  return sources;
}

/** Synthetic only: insert cloned statuses before the first view, translating both sequence references. */
export function coldStartStress(count: number): ServerEvent[] {
  const events = chatEvents();
  const at = events.findIndex((event) => event.type === "lead.view");
  // Repeat the final coding status so the stress prefix preserves preview eligibility.
  const status = events.findLast((event) => event.type === "agent.status");
  if (!status) throw new Error("Missing stress status");
  return [
    ...events.slice(0, at),
    ...Array.from({ length: count }, () => structuredClone(status)),
    ...events.slice(at),
  ].map((event, i) => ({
    ...event,
    seq: i + 1,
    payload:
      event.type === "lead.view" || event.type === "lead.reply"
        ? {
            ...event.payload,
            through_seq:
              event.payload.through_seq === null
                ? null
                : event.payload.through_seq + (event.payload.through_seq >= at ? count : 0),
          }
        : event.payload,
  })) as ServerEvent[];
}

/** Serve an existing scripted-fake live recording through the actual Python SSE route/store.
 * A test-owned release endpoint separates a known cold prefix from later committed arrivals.
 */
export async function coldStartApi() {
  const code = `
import asyncio, json, socket, sys, tempfile
from pathlib import Path
import uvicorn
from oversight.app import create_app
from oversight.settings import Settings
async def main():
    with tempfile.TemporaryDirectory(prefix="oversight-depth-live-") as directory:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        settings = Settings(runtime_dir=Path(directory) / ".runtime", port=port,
            agent_model="fake", fake_script=Path("fixtures/fake-model/document-apply-coupon.json").resolve(),
            fake_script_b=Path("fixtures/fake-model/document-restock.json").resolve(),
            demo_python=Path(sys.executable), layout=Path("fixtures/layouts/demo-repo.json").resolve())
        app = create_app(settings)
        @app.get("/depth-log")
        async def log():
            return [json.loads(event.envelope) for event in app.state.store._events]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", proxy_headers=False, timeout_graceful_shutdown=1))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if task.done(): await task
            await asyncio.sleep(0.01)
        print(port, flush=True)
        await task
asyncio.run(main())
`;
  const child = spawn("uv", ["run", "--frozen", "python", "-u", "-c", code], {
    cwd: fileURLToPath(new URL("../../../../", import.meta.url)),
    timeout: 120_000,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let errors = "";
  child.stderr.on("data", (chunk: Buffer) => {
    errors += chunk.toString();
  });
  const lines = createInterface({ input: child.stdout });
  const port = await Promise.race([
    once(lines, "line").then((values: unknown[]) => Number(values[0])),
    once(child, "exit").then(() => {
      throw new Error(`SSE server exited: ${errors}`);
    }),
  ]);
  if (!Number.isInteger(port) || port <= 0) throw new Error("Missing real SSE API port");
  return {
    url: `http://127.0.0.1:${String(port)}`,
    close: async () => {
      child.kill();
      await once(child, "exit");
      lines.close();
    },
  };
}

export function readSsePrefix(url: string, count: number, headers: Record<string, string> = {}) {
  return new Promise<{ events: ServerEvent[]; chunks: number[] }>((resolve, reject) => {
    const events: ServerEvent[] = [],
      chunks: number[] = [];
    let pending = "";
    const request = httpRequest(url, { headers, timeout: 10_000 }, (response) => {
      response.setEncoding("utf8");
      response.on("data", (text: string) => {
        pending += text;
        const frames = pending.split(/\r?\n\r?\n/);
        pending = frames.pop() ?? "";
        let delivered = 0;
        for (const frame of frames) {
          const data = frame.split(/\r?\n/).find((line) => line.startsWith("data: "));
          if (data) {
            events.push(JSON.parse(data.slice(6)) as ServerEvent);
            delivered++;
          }
        }
        chunks.push(delivered);
        if (events.length >= count) {
          resolve({ events, chunks });
          response.destroy();
          request.destroy();
        }
      });
      response.on("error", reject);
    });
    request.on("timeout", () => request.destroy(new Error("SSE prefix timed out")));
    request.on("error", reject);
    request.end();
  });
}
