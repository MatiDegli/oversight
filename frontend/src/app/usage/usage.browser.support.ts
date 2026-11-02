import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page, type Route } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect } from "vitest";
import type { UsageView } from "@contracts";
import type { GraphProbeHost } from "../../graph";
import { launchBrowser } from "../../browserLaunch.support";
import { recorded } from "./usage.fixture";

export const directory = fileURLToPath(
  new URL("../../../../.shots/usage-screen/", import.meta.url),
);

export function usageBrowserSupport(cachePrefix = "") {
  let browser: Browser;
  const servers: ViteDevServer[] = [];
  const entries: { url: string; html: string }[] = [];
  beforeAll(async () => {
    for (const mode of ["development", "replay"]) {
      const server = await createServer({
        root: fileURLToPath(new URL("../../../", import.meta.url)),
        mode,
        cacheDir: `${directory}${cachePrefix}${mode}-cache`,
        server: { host: "127.0.0.1", port: 0, strictPort: false },
      });
      servers.push(server);
      const html = await server.transformIndexHtml(
        "/",
        '<html><body><div id="root"></div><script type="module">import "/src/theme/index.css"; import "/src/app/usage/usage.browser.fixture.tsx";</script></body></html>',
      );
      await server.listen();
      entries.push({ url: server.resolvedUrls?.local[0] ?? "", html });
    }
    browser = await launchBrowser();
  }, 60_000);
  afterAll(async () => {
    await browser.close();
    await Promise.all(servers.map((server) => server.close()));
  });
  async function open(
    data: UsageView = recorded,
    options: {
      width?: number;
      theme?: string;
      query?: string;
      replay?: boolean;
      handler?: (route: Route) => Promise<void>;
    } = {},
  ) {
    const entry = entries[options.replay ? 1 : 0];
    if (!entry) throw new Error("Missing server");
    const page = await browser.newPage({
      viewport: { width: options.width ?? 1920, height: options.width === 1280 ? 800 : 1080 },
      reducedMotion: "reduce",
    });
    await page.route("**/usage", options.handler ?? ((route) => route.fulfill({ json: data })));
    await page.route("**/?*", (route) =>
      route.fulfill({ contentType: "text/html", body: entry.html }),
    );
    await page.route("**/", (route) =>
      route.fulfill({ contentType: "text/html", body: entry.html }),
    );
    await page.goto(`${entry.url}?theme=${options.theme ?? "dark"}&${options.query ?? ""}`);
    await check(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
    await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
    }, options.theme ?? "dark");
    return page;
  }
  return { open };
}

export function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Missing fixture value");
  return value;
}
export const enter = (page: Page) =>
  page.getByRole("button", { name: "Usage", exact: true }).click();
export const refresh = (page: Page, runId?: string) =>
  page.evaluate((runId) => {
    (window as unknown as { usageTest: { refresh: (runId?: string) => void } }).usageTest.refresh(
      runId,
    );
  }, runId);
export async function camera(page: Page) {
  await check
    .poll(() =>
      page
        .locator('[aria-label="Repository graph"]')
        .evaluate((host: GraphProbeHost) => Boolean(host.oversightGraph?.nodeCenters().length)),
    )
    .toBe(true);
  return page.locator('[aria-label="Repository graph"]').evaluate((host: GraphProbeHost) => ({
    pan: host.oversightGraph?.pan(),
    zoom: host.oversightGraph?.zoom(),
    positions: host.oversightGraph?.positions(),
    selected: host.oversightGraph?.appearance(":selected").map((node) => node.id),
  }));
}
export const row = (page: Page, name: string) =>
  page.getByRole("row").filter({ has: page.getByRole("rowheader", { name, exact: false }) });

export async function groupingStyle(page: Page) {
  const selected = page.locator('.usage-grouping button[aria-pressed="true"]');
  const styles = await selected.evaluate((button) => {
    const probe = document.createElement("span");
    probe.style.color = "var(--text)";
    button.append(probe);
    const text = getComputedStyle(probe).color;
    probe.remove();
    const style = getComputedStyle(button);
    return {
      text,
      underline: [style.borderBottomWidth, style.borderBottomStyle, style.borderBottomColor],
      focus: [style.outlineWidth, style.outlineStyle, style.outlineOffset, style.outlineColor],
    };
  });
  expect(styles.underline).toEqual(["2px", "solid", styles.text]);
  await check(selected).toBeFocused();
  expect(styles.focus).toEqual(["2px", "solid", "2px", styles.text]);
}

export async function coverageStyles(page: Page) {
  const chips = await page.locator(".usage-coverage").evaluateAll((elements) =>
    elements.map((chip) => {
      const label = chip.textContent;
      const probe = document.createElement("span");
      probe.style.color = label === "Incomplete" ? "var(--failed-text)" : "var(--muted)";
      chip.append(probe);
      const expected = getComputedStyle(probe).color;
      probe.remove();
      const icon = chip.querySelector("svg");
      if (!icon) throw new Error("Missing coverage icon");
      return {
        label,
        expected,
        color: getComputedStyle(chip).color,
        icon: getComputedStyle(icon).stroke,
      };
    }),
  );
  expect(chips).toHaveLength(2);
  for (const chip of chips) {
    expect(chip.color).toBe(chip.expected);
    expect(chip.icon).toBe(chip.expected);
  }
}
