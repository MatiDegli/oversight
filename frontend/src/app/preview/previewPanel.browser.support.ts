import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { expect as check, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect } from "vitest";
import { launchBrowser } from "../../browserLaunch.support";
import { recorded } from "../usage/usage.fixture";
import { deferred, fakeCandidate, PreviewFake, PreviewFakeClock } from "./previewFake";
import { previewReasons } from "./previewTypes";
import type { FakeRequest } from "./PreviewPanel.browser.fixture";

export const shotsDirectory = fileURLToPath(
  new URL("../../../../.shots/preview-chat/fixture/", import.meta.url),
);
/** Follow the repository's bounded asyncio Git convention; never guess a capture revision. */
export async function captureCheckpoint() {
  const { stdout } = await promisify(execFile)(
    "python3",
    [
      "-c",
      `import asyncio, json
async def git(*args):
    process = await asyncio.create_subprocess_exec(
        "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(process.communicate(), 5)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise RuntimeError(err.decode())
    return out.decode().strip()
async def main():
    head = await git("rev-parse", "--verify", "HEAD")
    dirty = await git("status", "--porcelain", "--untracked-files=normal")
    print(json.dumps({"checkpoint": head, "workingTree": bool(dirty)}))
asyncio.run(main())`,
    ],
    { cwd: fileURLToPath(new URL("../../../../", import.meta.url)), timeout: 15_000 },
  );
  const checkpoint = JSON.parse(stdout) as { checkpoint: string; workingTree: boolean };
  if (!/^[a-f0-9]{40}$/.test(checkpoint.checkpoint)) throw new Error("Invalid capture HEAD");
  return checkpoint;
}
export const scenarios = [
  "starting",
  "ready",
  "stopping-closed",
  "stopped-closed",
  "stopped-lease_expired",
  "stopped-lifetime_expired",
  ...previewReasons.map((reason) => `failed-${reason}`),
  "observer",
  "isolation-before-launch",
  "failed-stop",
];
export const sizes = [
  { width: 1920, height: 1080 },
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
];
export interface FrameManifest {
  path: string;
  scenario: string;
  viewport: { width: number; height: number };
  theme: string;
  pageErrors: string[];
  overflow: { page: number; panel: number };
}
export function previewBrowserSupport(suffix: string) {
  const support = new BrowserSupport(suffix);
  beforeAll(() => support.start(), 60_000);
  afterAll(() => support.dispose());
  return support;
}
class BrowserSupport {
  server?: ViteDevServer;
  browser?: Browser;
  html = "";
  url = "";
  contexts = new Set<BrowserContext>();
  errors = new WeakMap<Page, string[]>();
  diagnostics = new WeakMap<Page, { type: string; text: string }[]>();
  constructor(readonly suffix: string) {}
  async start() {
    const server = await createServer({
      appType: "custom",
      root: fileURLToPath(new URL("../../../", import.meta.url)),
      cacheDir: `${shotsDirectory}cache-${this.suffix}`,
      optimizeDeps: { entries: ["src/app/preview/PreviewPanel.browser.fixture.tsx"] },
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    this.server = server;
    this.html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html><body><div id="root"></div><script type="module">import "/src/theme/index.css"; import "/src/app/preview/PreviewPanel.browser.fixture.tsx";</script></body></html>',
    );
    server.middlewares.use((request, response, next) => {
      if (request.url?.split("?")[0] !== "/") {
        next();
        return;
      }
      response.setHeader("Content-Type", "text/html");
      // The isolated fixture serves exactly one synthetic frame origin; production keeps its pool policy.
      response.setHeader("Content-Security-Policy", "frame-src https://preview-frame.invalid");
      response.end(this.html);
    });
    await server.listen();
    this.url = server.resolvedUrls?.local[0] ?? "";
    if (!this.url) throw new Error("Missing preview fixture URL");
    this.browser = await launchBrowser();
    await mkdir(`${shotsDirectory}comparisons`, { recursive: true });
  }
  async dispose() {
    await Promise.all([...this.contexts].map((context) => context.close()));
    await this.browser?.close();
    await this.server?.close();
  }
  async session(
    options: { width?: number; height?: number; theme?: string; scenario?: string } = {},
  ) {
    const { browser, url, contexts, errors, diagnostics } = this;
    if (!browser) throw new Error("Preview browser is unavailable");
    const clock = new PreviewFakeClock();
    const authority = new PreviewFake(clock);
    const scenario = options.scenario ?? "ready";
    authority.launchState = ["ready", "stopping-closed", "failed-stop", "observer"].includes(
      scenario,
    )
      ? "ready"
      : "starting";
    if (scenario === "failed-stop") authority.stopMode = "reject";
    if (scenario === "stopping-closed") authority.gate = deferred<undefined>();
    if (scenario === "observer") await authority.start(fakeCandidate, new AbortController().signal);
    if (scenario === "isolation-before-launch")
      authority.publish("failed", "isolation_unavailable");
    const context = await browser.newContext({
      viewport: { width: options.width ?? 1920, height: options.height ?? 1080 },
      reducedMotion: "reduce",
    });
    contexts.add(context);
    await context.exposeBinding("previewBoundary", (_source, request: FakeRequest) =>
      dispatch(authority, request),
    );
    await this.route(context, options.theme ?? "dark", url);
    async function open(mode = "panel", extra = "", selectedScenario = scenario) {
      const page = await context.newPage();
      const pageErrors: string[] = [];
      errors.set(page, pageErrors);
      const consoleMessages: { type: string; text: string }[] = [];
      diagnostics.set(page, consoleMessages);
      page.on("pageerror", (error) => pageErrors.push(error.message));
      page.on("console", (message) => {
        consoleMessages.push({ type: message.type(), text: message.text() });
        if (message.type() === "error") pageErrors.push(message.text());
      });
      await page.goto(
        `${url}?mode=${mode}&theme=${options.theme ?? "dark"}&scenario=${selectedScenario}&${extra}`,
      );
      await page
        .waitForFunction(() => window.previewFixture.ready(), undefined, { timeout: 10_000 })
        .catch(() => {
          throw new Error(`Fixture did not mount: ${pageErrors.join("; ")}`);
        });
      await page.evaluate(() => document.fonts.ready);
      if (mode === "review") {
        await check(page.getByLabel("Review panel", { exact: true })).toBeVisible();
        await check(page.locator(".monaco-diff-editor")).toBeVisible({ timeout: 30_000 });
      }
      if ((mode === "usage" || mode === "settings") && (options.width ?? 1920) >= 1280) {
        await check(
          page.getByRole("tab", { name: mode === "usage" ? "Usage" : "Settings", exact: true }),
        ).toBeVisible();
        if (mode === "usage") await check(page.locator(".usage-scope")).toBeVisible();
        else
          await check(
            page.getByText("Every change waits for your decision.", { exact: true }),
          ).toBeVisible();
      }
      if (mode === "usage") await check(page.locator(".usage-scope")).toHaveCount(1);
      if (mode !== "panel" && !extra.includes("chat"))
        await check(page.getByText("Loading repository graph…", { exact: true })).toHaveCount(0);
      return page;
    }
    async function close() {
      await context.close();
      contexts.delete(context);
    }
    return { authority, clock, context, open, close };
  }
  async route(context: BrowserContext, theme: string, url: string) {
    await context.route("**/*", async (route) => {
      const request = route.request(),
        target = new URL(request.url());
      if (target.origin === "https://preview-frame.invalid")
        return route.fulfill({
          contentType: "text/html",
          body: `<html style="color-scheme:${theme === "day" ? "light" : "dark"}"><body style="font:16px sans-serif;padding:24px"><h1>Preview app</h1><p>Handwritten frame response at a distinct origin.</p><form><label>Quantity <input type="number" value="3"></label></form></body></html>`,
        });
      if (target.origin !== new URL(url).origin) throw new Error("Unexpected external request");
      if (target.pathname === "/usage") return route.fulfill({ json: recorded });
      return route.continue();
    });
  }
  async metrics(page: Page) {
    const { errors } = this;
    return page.evaluate(
      (pageErrors) => {
        const panels = [
          ...document.querySelectorAll<HTMLElement>(
            ".preview-panel, .preview-panel-body, .review-panel, .review-body, .focus-panel, .usage-scroll",
          ),
        ];
        const overflow = (node: HTMLElement | null) => {
          // The full IDE allocates a zero-width focus panel below 1280; its hidden
          // children's intrinsic widths are not overflow of a visible panel.
          if (
            !node ||
            node.clientWidth === 0 ||
            node.closest<HTMLElement>(".focus-panel")?.clientWidth === 0
          )
            return 0;
          return Math.max(0, node.scrollWidth - node.clientWidth);
        };
        return {
          pageErrors,
          overflow: {
            page: overflow(document.documentElement),
            panel: Math.max(0, ...panels.map(overflow)),
          },
        };
      },
      errors.get(page) ?? [],
    );
  }
  async capture(page: Page, scenario: string, viewport: FrameManifest["viewport"], theme: string) {
    const path = `isolated-${scenario}-${String(viewport.width)}-${theme}.png`;
    await page.evaluate(() => document.fonts.ready);
    const checkpoint = await captureCheckpoint();
    await page.screenshot({ path: `${shotsDirectory}${path}` });
    const data = await this.metrics(page);
    await writeFile(
      `${shotsDirectory}${path.replace(/\.png$/, ".json")}`,
      JSON.stringify(
        {
          png: path,
          state: scenario,
          viewport,
          theme,
          ...checkpoint,
          provenance: "fixture · handwritten authority",
          candidate: await page.evaluate(() => window.previewFixture.snapshot().status),
          console: this.diagnostics.get(page) ?? [],
          appOverflow: await (async () => {
            const frame = page.frames().find((item) => item.parentFrame());
            return frame
              ? frame.evaluate(() =>
                  Math.max(
                    0,
                    document.documentElement.scrollWidth - document.documentElement.clientWidth,
                  ),
                )
              : "inapplicable";
          })(),
          overflowDetails: await page.evaluate(() =>
            Object.fromEntries(
              ["html", "body", ".conversation-panel", ".preview-panel"].map((selector) => {
                const node = document.querySelector(selector);
                return [
                  selector,
                  node?.clientWidth
                    ? Math.max(0, node.scrollWidth - node.clientWidth)
                    : "inapplicable",
                ];
              }),
            ),
          ),
          reducedMotion: "reduce",
          fontReady: await page.evaluate(() => document.fonts.status),
          rectangles: await page.evaluate(() =>
            Object.fromEntries(
              [".conversation-panel", ".preview-panel", "iframe"].map((selector) => [
                selector,
                (document.querySelector(selector)?.getBoundingClientRect().toJSON() as unknown) ??
                  "inapplicable",
              ]),
            ),
          ),
          ...data,
        },
        null,
        2,
      ) + "\n",
    );
    expect(data.pageErrors).toEqual([]);
    expect(data.overflow).toEqual({ page: 0, panel: 0 });
    return { path, scenario, viewport, theme, ...data } satisfies FrameManifest;
  }
  async compare(frame: FrameManifest, reference: FrameManifest) {
    const images = await Promise.all(
      [frame, reference].map((item) => readFile(`${shotsDirectory}${item.path}`)),
    );
    if (!this.browser) throw new Error("Preview browser is unavailable");
    const page = await this.browser.newPage({
      viewport: { width: frame.viewport.width * 2, height: frame.viewport.height + 44 },
    });
    try {
      await page.setContent(
        `<style>body{margin:0;font:14px sans-serif}.pair{display:flex}.pair figure{margin:0}figcaption{height:44px;display:grid;align-items:center;padding-inline:16px}img{display:block}</style><div class="pair">${images.map((data, index) => `<figure><figcaption>${index === 0 ? `Preview · ${frame.scenario}` : "Built reference · review"} · ${String(frame.viewport.width)} × ${String(frame.viewport.height)} · ${frame.theme}</figcaption><img alt="${index === 0 ? "Preview" : "Built review"}" src="data:image/png;base64,${data.toString("base64")}"></figure>`).join("")}</div>`,
      );
      await page
        .locator("img")
        .evaluateAll((nodes) =>
          Promise.all(nodes.map((node) => (node as HTMLImageElement).decode())),
        );
      await page.screenshot({ path: `${shotsDirectory}comparisons/${frame.path}`, fullPage: true });
    } finally {
      await page.close();
    }
  }
  async manifest(frames: FrameManifest[]) {
    await writeFile(
      `${shotsDirectory}manifest.json`,
      JSON.stringify(
        { provenance: "Handwritten fake authority; dormant preview only", frames },
        null,
        2,
      ) + "\n",
    );
  }
}
function dispatch(
  authority: PreviewFake,
  request: FakeRequest,
): Promise<unknown> | PreviewStatusOrNull {
  const signal = new AbortController().signal;
  switch (request.kind) {
    case "start":
      return authority.start(request.input, signal);
    case "details":
      return authority.details(request.id, signal);
    case "renew":
      return authority.renew(request.id, request.body, signal);
    case "stop":
      return authority.stop(request.id, request.body, signal);
    case "public":
      return authority.publicStatus();
    case "publish":
      return authority.publish(request.state, request.reason);
  }
}
type PreviewStatusOrNull = ReturnType<PreviewFake["publicStatus"]>;
