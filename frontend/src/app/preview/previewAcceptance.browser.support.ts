import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect } from "vitest";
import type { StartPreview } from "@contracts";
import { launchBrowser } from "../../browserLaunch.support";

export const directory = fileURLToPath(
  new URL("../../../../.shots/preview-acceptance/", import.meta.url),
);
export const sizes = [
  { width: 1920, height: 1080 },
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
];
export interface AcceptanceFrame {
  path: string;
  stage: string;
  viewport: { width: number; height: number };
  theme: string;
  utc: string;
  pageErrors: string[];
  overflow: { page: number; panel: number; frame: number };
}
export function acceptanceSupport(enabled = true) {
  const support = new AcceptanceSupport();
  beforeAll(() => (enabled ? support.start() : undefined), 60_000);
  afterAll(() => support.dispose());
  return support;
}
class AcceptanceSupport {
  server?: ViteDevServer;
  browser?: Browser;
  url = "";
  candidate?: StartPreview;
  contexts = new Set<BrowserContext>();
  async start() {
    const candidatePath = process.env.OVERSIGHT_PREVIEW_ACCEPTANCE_CANDIDATE;
    if (!candidatePath)
      throw new Error("Run through python -m tools.preview_browser with a real C backend");
    this.candidate = JSON.parse(await readFile(candidatePath, "utf8")) as StartPreview;
    const server = await createServer({
      appType: "custom",
      root: fileURLToPath(new URL("../../../", import.meta.url)),
      cacheDir: `${directory}vite-cache`,
      optimizeDeps: { entries: ["src/app/preview/PreviewAcceptance.browser.fixture.tsx"] },
    });
    this.server = server;
    const html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module">import "/src/theme/index.css"; import "/src/app/preview/PreviewAcceptance.browser.fixture.tsx";</script></body></html>',
    );
    server.middlewares.use((request, response, next) => {
      if (request.url?.split("?")[0] !== "/acceptance") {
        next();
        return;
      }
      response.setHeader("Content-Type", "text/html");
      response.end(html);
    });
    await server.listen();
    this.url = server.resolvedUrls?.local[0] ?? "";
    if (!this.url) throw new Error("Acceptance Vite URL is absent");
    this.browser = await launchBrowser();
    await mkdir(`${directory}comparisons`, { recursive: true });
  }
  async dispose() {
    await Promise.all([...this.contexts].map((context) => context.close()));
    await this.browser?.close();
    await this.server?.close();
  }
  async session(viewport = sizes[0], theme = "dark") {
    if (!this.browser || !viewport || !this.candidate)
      throw new Error("Acceptance browser is unavailable");
    const context = await this.browser.newContext({ viewport, reducedMotion: "reduce" });
    context.setDefaultTimeout(5_000);
    this.contexts.add(context);
    const errors: string[] = [];
    const security: string[] = [];
    const responses: { url: string; method: string; headers: Record<string, string> }[] = [];
    const open = async () => {
      const page = await context.newPage();
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() !== "error" || message.location().url.endsWith("/@vite/client")) return;
        if (
          /Content Security Policy|sandboxed|Blocked|Unsafe attempt|Refused|not allowed/.test(
            message.text(),
          )
        )
          security.push(message.text());
        else errors.push(message.text());
      });
      page.on("response", (response) => {
        if (response.url().startsWith("http://127.0.0.1:") && !response.url().startsWith(this.url))
          responses.push({
            url: response.url(),
            method: response.request().method(),
            headers: response.headers(),
          });
      });
      await page.goto(
        `${this.url}acceptance?theme=${theme}&candidate=${encodeURIComponent(JSON.stringify(this.candidate))}`,
      );
      await page
        .waitForFunction(() => Boolean(window.previewAcceptance), undefined, {
          timeout: 10_000,
        })
        .catch(() => {
          throw new Error(`Acceptance fixture did not mount: ${errors.join("; ")}`);
        });
      await page.evaluate(() => document.fonts.ready);
      return page;
    };
    const close = async () => {
      for (const page of context.pages())
        await page.evaluate(() => window.previewAcceptance.close()).catch(() => undefined);
      await context.close();
      this.contexts.delete(context);
    };
    return { context, open, close, errors, security, responses };
  }
  async ready(page: Page) {
    await page.getByRole("button", { name: "See preview", exact: true }).click();
    await check
      .poll(
        async () => {
          await page.evaluate(() => window.previewAcceptance.refresh());
          return page.evaluate(() => window.previewAcceptance.snapshot().status?.state);
        },
        { timeout: 15_000 },
      )
      .toBe("ready");
    const frame = page.frameLocator("iframe");
    await check(frame.getByRole("heading", { name: "Candidate shop" })).toBeVisible();
    await check(page.locator("iframe")).toHaveAttribute("sandbox", "allow-forms");
    await check(page.locator("iframe")).toHaveAttribute("referrerpolicy", "no-referrer");
    return frame;
  }
  async capture(
    page: Page,
    stage: string,
    viewport: AcceptanceFrame["viewport"],
    theme: string,
    pageErrors: string[],
  ): Promise<AcceptanceFrame> {
    const path = `${stage}-${String(viewport.width)}-${theme}.png`;
    const overflow = await page.evaluate(() => {
      const over = (element: Element | null) =>
        element ? Math.max(0, element.scrollWidth - element.clientWidth) : 0;
      return {
        page: over(document.documentElement),
        panel: over(document.querySelector(".preview-panel-body")),
      };
    });
    const frame = page.frames().find((item) => item.parentFrame());
    if (!frame) throw new Error("Missing application frame");
    const frameOverflow = await frame.evaluate(() =>
      Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
    );
    expect(pageErrors).toEqual([]);
    expect({ ...overflow, frame: frameOverflow }).toEqual({ page: 0, panel: 0, frame: 0 });
    await page.screenshot({ path: `${directory}${path}` });
    return {
      path,
      stage,
      viewport,
      theme,
      utc: new Date().toISOString(),
      pageErrors: [...pageErrors],
      overflow: { ...overflow, frame: frameOverflow },
    };
  }
  async compare(before: AcceptanceFrame, after: AcceptanceFrame) {
    if (!this.browser) throw new Error("Missing browser");
    const images = await Promise.all(
      [before, after].map((frame) => readFile(`${directory}${frame.path}`)),
    );
    const page = await this.browser.newPage({
      viewport: { width: before.viewport.width * 2, height: before.viewport.height + 44 },
    });
    try {
      await page.setContent(
        `<style>body{margin:0;font:14px sans-serif}.pair{display:flex}figure{margin:0}figcaption{height:44px}img{display:block}</style><div class="pair">${images.map((data, index) => `<figure><figcaption>${index === 0 ? "Before DEMO10 · 1500 cents" : "After DEMO10 · 1350 cents"}</figcaption><img alt="Candidate shop comparison" src="data:image/png;base64,${data.toString("base64")}"></figure>`).join("")}</div>`,
      );
      await page
        .locator("img")
        .evaluateAll((nodes) =>
          Promise.all(nodes.map((node) => (node as HTMLImageElement).decode())),
        );
      await page.screenshot({ path: `${directory}comparisons/${after.path}`, fullPage: true });
    } finally {
      await page.close();
    }
  }
  async manifest(frames: AcceptanceFrame[]) {
    await writeFile(
      `${directory}manifest.json`,
      JSON.stringify(
        {
          provenance: "Isolated preview acceptance · scripted fake model",
          completedUTC: new Date().toISOString(),
          frames,
        },
        null,
        2,
      ) + "\n",
    );
  }
}
