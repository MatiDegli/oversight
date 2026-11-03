import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { launchBrowser } from "../../browserLaunch.support";
import { openOversight } from "../../workspaceView.browser.support";
import type { PreviewStartupReport } from "./previewStartup";

export const startupFixture: PreviewStartupReport = {
  version: 1,
  argv: [
    ["pnpm", "run", "build"],
    ["node", "server.js", "--port", "8123", "--public-path=/" + "segment".repeat(18)],
  ],
  cwd: "/runtime/reviewed-app/应用/" + "directory".repeat(10),
  reason: "Selected package.json build and start scripts.",
  dependencies: ["Prepared node_modules from the locked offline store."],
  limits: "256 MiB memory; 8 processes; 300 s lifetime",
  platform_residual: "",
  build_log: "/runtime/logs/" + "candidate/".repeat(10) + "build.log",
  enable_hint: "Set OVERSIGHT_PREVIEW_ENABLED=1 before starting Oversight.",
  agents_label: "Agent A · reviewed candidate",
};
export const hostileText =
  '</script><script>window.previewInjected=true</script><!--<img id="preview-injected" src="/preview-injection" onerror="location.assign(\'/preview-navigation\')"><a href="/preview-navigation">go</a>&\\\u2028\u2029';
export const hostileFixture = {
  ...startupFixture,
  argv: [["node", "server.js"]],
  cwd: "/应用/é🙂",
  build_log: "/runtime/build.log",
  reason: hostileText,
};
export function carrierText(report: PreviewStartupReport) {
  return JSON.stringify(report).replace(
    /[<>&\u2028\u2029]/gu,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}
export const fixtureTexts = {
  valid: carrierText(startupFixture),
  missing: undefined,
  invalid: "{}",
  oversized: carrierText({ ...startupFixture, reason: "x".repeat(1025) }),
  hostile: carrierText(hostileFixture),
};
export type StartupScenario = keyof typeof fixtureTexts;
export interface StartupSession {
  page: Page;
  pageErrors: string[];
  unexpectedRequests: string[];
  navigations: string[];
  dialogs: string[];
}
export const shotsDirectory = fileURLToPath(
  new URL("../../../../.shots/preview-startup/", import.meta.url),
);

export class StartupBrowser {
  private server?: ViteDevServer;
  private browser?: Browser;
  private html = "";
  private url = "";

  async start() {
    this.server = await createServer({
      root: fileURLToPath(new URL("../../../", import.meta.url)),
      cacheDir: `${shotsDirectory}cache`,
      plugins: [
        {
          name: "preview-startup-fixture-events",
          configureServer(server) {
            server.middlewares.use("/events", (_request, response) => {
              response.writeHead(200, { "Content-Type": "text/event-stream" });
              response.write(": connected\n\n");
            });
          },
        },
      ],
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    this.html = await this.server.transformIndexHtml(
      "/",
      '<!doctype html><html><head><meta charset="UTF-8"></head><body><!--preview-startup-carrier--><div id="root"></div><script type="module">import "/src/theme/index.css"; import "/src/app/settings.browser.fixture.tsx";</script></body></html>',
    );
    await this.server.listen();
    this.url = this.server.resolvedUrls?.local[0] ?? "";
    if (!this.url) throw new Error("Missing startup fixture URL");
    this.browser = await launchBrowser();
  }

  async close() {
    await this.browser?.close();
    await this.server?.close();
  }

  async open(scenario: StartupScenario): Promise<StartupSession> {
    if (!this.browser) throw new Error("Missing startup fixture browser");
    const page = await this.browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    const session: StartupSession = {
      page,
      pageErrors: [],
      unexpectedRequests: [],
      navigations: [],
      dialogs: [],
    };
    page.on("pageerror", (error) => session.pageErrors.push(error.message));
    page.on("dialog", async (dialog) => {
      session.dialogs.push(dialog.message());
      await dialog.dismiss();
    });
    page.on("request", (request) => {
      if (/\/preview-(injection|navigation)/u.test(request.url()))
        session.unexpectedRequests.push(request.url());
    });
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame()) session.navigations.push(frame.url());
    });
    const text = fixtureTexts[scenario];
    const carrier =
      text === undefined
        ? ""
        : `<script type="application/json" id="oversight-preview-startup">${text}</script>`;
    await page.route("**/", (route) =>
      route.fulfill({
        contentType: "text/html; charset=utf-8",
        body: this.html.replace("<!--preview-startup-carrier-->", carrier),
      }),
    );
    await page.goto(this.url);
    await openOversight(page);
    const settings = page.getByRole("button", { name: "Settings", exact: true });
    await check(settings).toBeVisible();
    await settings.focus();
    await check(page.getByRole("tab", { name: "Settings", exact: true })).toHaveCount(0);
    await check(page.getByRole("region", { name: "Preview startup", exact: true })).toHaveCount(0);
    await check(settings).toBeFocused();
    await settings.click();
    await check(page.getByRole("region", { name: "Preview startup", exact: true })).toBeVisible();
    return session;
  }
}

export async function captureStartup(
  session: StartupSession,
  scenario: StartupScenario,
  viewport: { width: number; height: number },
  theme: string,
) {
  const { page } = session;
  await check(page.getByText("Loading repository graph...", { exact: true })).toHaveCount(0);
  // Use the existing maximize action before resizing the desktop shell to phone width.
  if (viewport.width === 390)
    await page.getByRole("button", { name: "Maximize focus panel", exact: true }).click();
  await page.setViewportSize(viewport);
  await page.evaluate(
    ({ theme, scenario }) => {
      document.documentElement.dataset.theme = theme;
      const label = document.createElement("aside");
      label.textContent = `Fixture · Preview startup · ${scenario}`;
      label.setAttribute("aria-label", "Fixture evidence");
      label.style.cssText =
        "position:fixed;bottom:30px;right:8px;z-index:10000;padding:4px 8px;border:1px solid var(--page-border);background:var(--page);color:var(--muted);font-size:var(--caption-size)";
      document.body.append(label);
      return document.fonts.ready.then(() => undefined);
    },
    { theme, scenario },
  );
  const panel = page.getByRole("region", { name: "Preview startup", exact: true });
  if (viewport.width === 390) {
    // Position the evidence explicitly; scenario tests check that arrival leaves scroll at zero.
    await panel.evaluate((element) => {
      const frame = element.parentElement;
      if (frame)
        frame.scrollTop +=
          element.getBoundingClientRect().top - frame.getBoundingClientRect().top - 32;
    });
  }
  const overflow = await panel.evaluate((element) => ({
    page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    panel: element.scrollWidth - element.clientWidth,
    values: Math.max(
      0,
      ...[...element.querySelectorAll("dd, code, li")].map(
        (node) => node.scrollWidth - node.clientWidth,
      ),
    ),
  }));
  await mkdir(shotsDirectory, { recursive: true });
  const stem = `${scenario}-${String(viewport.width)}x${String(viewport.height)}-${theme}-fixture`;
  await page.screenshot({ path: `${shotsDirectory}${stem}.png` });
  await writeFile(
    `${shotsDirectory}${stem}.json`,
    JSON.stringify(
      {
        evidence: "fixture",
        scenario,
        viewport,
        theme,
        presentation:
          viewport.width === 390
            ? "existing maximize action, resize, manual scroll to disclosure"
            : "existing Settings navigation",
        pageErrors: session.pageErrors,
        horizontalOverflow: overflow,
      },
      null,
      2,
    ) + "\n",
  );
  return overflow;
}
