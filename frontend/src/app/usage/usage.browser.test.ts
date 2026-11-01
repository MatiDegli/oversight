import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page, type Route } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type { UsageView } from "@contracts";
import type { GraphProbeHost } from "../../graph";
import { launchBrowser } from "../../browserLaunch.support";
import { recorded, early, hosted, lead, empty } from "./usage.fixture";

function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Missing fixture value");
  return value;
}
let browser: Browser;
const servers: ViteDevServer[] = [];
const entries: { url: string; html: string }[] = [];
const directory = fileURLToPath(new URL("../../../../.shots/usage-screen/", import.meta.url));
beforeAll(async () => {
  for (const mode of ["development", "replay"]) {
    const server = await createServer({
      root: fileURLToPath(new URL("../../../", import.meta.url)),
      mode,
      cacheDir: `${directory}${mode}-cache`,
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
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: entry.html }));
  await page.goto(`${entry.url}?theme=${options.theme ?? "dark"}&${options.query ?? ""}`);
  await check(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
  await page.evaluate((theme) => {
    document.documentElement.dataset.theme = theme;
  }, options.theme ?? "dark");
  return page;
}
const enter = (page: Page) => page.getByRole("button", { name: "Usage", exact: true }).click();
const refresh = (page: Page, runId?: string) =>
  page.evaluate((runId) => {
    (window as unknown as { usageTest: { refresh: (runId?: string) => void } }).usageTest.refresh(
      runId,
    );
  }, runId);
async function camera(page: Page) {
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
const row = (page: Page, name: string) =>
  page.getByRole("row").filter({ has: page.getByRole("rowheader", { name, exact: false }) });

it("fetches only on opening, preserves the camera, graph identity, selection and focus", async () => {
  let requests = 0;
  const page = await open(recorded, {
    handler: (route) => {
      requests++;
      return route.fulfill({ json: recorded });
    },
  });
  try {
    const before = await camera(page);
    expect(requests).toBe(0);
    expect(before.selected).toHaveLength(1);
    const host = await page.getByLabel("Repository graph", { exact: true }).elementHandle();
    const usage = page.getByRole("button", { name: "Usage", exact: true });
    await usage.focus();
    await page.keyboard.press("Enter");
    await check(page.locator(".usage-scope")).toHaveText("Up to event 69");
    expect(requests).toBe(1);
    await check(usage).toBeFocused();
    expect(await camera(page)).toEqual(before);
    const model = page.getByRole("button", { name: "By model", exact: true });
    await model.focus();
    await page.keyboard.press("Space");
    await check(model).toHaveAttribute("aria-pressed", "true");
    await refresh(page);
    await check.poll(() => requests).toBe(2);
    await check(model).toHaveAttribute("aria-pressed", "true");
    await check(model).toBeFocused();
    for (const name of ["Maximize focus panel", "Show graph"])
      await page.getByRole("button", { name, exact: true }).click();
    expect(await camera(page)).toEqual(before);
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const settings = await camera(page);
    await page.getByRole("tab", { name: "Usage", exact: true }).click();
    expect(await camera(page)).toEqual(settings);
    expect(
      await host.evaluate(
        (node) => node === document.querySelector('[aria-label="Repository graph"]'),
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "Close tab Usage", exact: true }).click();
    await enter(page);
    await check(page.locator(".usage-scope")).toBeVisible();
    expect(requests).toBe(4);
  } finally {
    await page.close();
  }
});
it("shows loading, HTTP error and retry without invented values; rejects stale runs and requests", async () => {
  const pending: Route[] = [];
  const page = await open(recorded, {
    handler: (route) => {
      pending.push(route);
      return Promise.resolve();
    },
  });
  try {
    await enter(page);
    await check(page.getByRole("status").filter({ hasText: "Loading usage" })).toBeVisible();
    await check.poll(() => pending.length).toBe(1);
    await required(pending[0]).fulfill({
      status: 503,
      json: { error: { message: "Aggregate unavailable" } },
    });
    await check(page.getByRole("alert")).toContainText("Aggregate unavailable");
    await check(page.getByText("US$0", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Retry usage" }).click();
    await check.poll(() => pending.length).toBe(2);
    await refresh(page);
    await check.poll(() => pending.length).toBe(3);
    await required(pending[2]).fulfill({ json: { ...recorded, seq: 71 } });
    await check(page.locator(".usage-scope")).toHaveText("Up to event 71");
    await required(pending[1])
      .fulfill({ json: { ...recorded, seq: 70 } })
      .catch(() => undefined);
    await check(page.locator(".usage-scope")).toHaveText("Up to event 71");
    await refresh(page, "run-next");
    await check.poll(() => pending.length).toBe(4);
    await check(page.locator(".usage-scope")).toHaveCount(0);
    await required(pending[3]).fulfill({ json: recorded });
    await check(page.getByRole("alert")).toContainText("another run");
    await page.getByRole("button", { name: "Retry usage" }).click();
    await check.poll(() => pending.length).toBe(5);
    await page.getByRole("button", { name: "Close tab Usage", exact: true }).click();
    await required(pending[4])
      .fulfill({ json: { ...recorded, run_id: "run-next", seq: 99 } })
      .catch(() => undefined);
    await enter(page);
    await check.poll(() => pending.length).toBe(6);
    await required(pending[5]).fulfill({ json: { ...empty, run_id: "run-next" } });
    await check(page.locator(".usage-closing")).toContainText("0 agents");
    await check(page.locator(".usage-scope")).toHaveText("Up to event 1");
  } finally {
    await page.close();
  }
});
it("retains the initial-panel camera on close and waits for an absent run", async () => {
  const page = await open(recorded, { query: "initial" });
  try {
    await check(page.locator(".usage-scope")).toBeVisible();
    const initial = await camera(page);
    await page.getByRole("button", { name: "Close tab Usage", exact: true }).click();
    expect(await camera(page)).toEqual(initial);
  } finally {
    await page.close();
  }
  let requests = 0;
  const absent = await open(recorded, {
    query: "no-run",
    handler: (route) => {
      requests++;
      return route.fulfill({ json: recorded });
    },
  });
  try {
    await enter(absent);
    await check(absent.getByText("Usage is available when a run starts.")).toBeVisible();
    expect(requests).toBe(0);
  } finally {
    await absent.close();
  }
});
it("recovers from timeout, offline and unreadable responses without leaking a run's error", async () => {
  const pending: Route[] = [];
  const page = await open(recorded, {
    handler: (route) => {
      pending.push(route);
      return Promise.resolve();
    },
  });
  try {
    await page.clock.install();
    await enter(page);
    await check.poll(() => pending.length).toBe(1);
    await page.clock.fastForward(15_001);
    await check(page.getByRole("alert")).toContainText("Usage took too long to load. Try again.");
    await refresh(page, "run-next");
    await check.poll(() => pending.length).toBe(2);
    await check(page.getByRole("alert")).toHaveCount(0);
    await check(page.getByRole("status").filter({ hasText: "Loading usage" })).toBeVisible();
    await required(pending[1]).abort("internetdisconnected");
    await check(page.getByRole("alert")).toContainText("Check the connection and try again.");
    for (const [index, status] of [503, 200, 200].entries()) {
      await page.getByRole("button", { name: "Retry usage" }).click();
      await check.poll(() => pending.length).toBe(index + 3);
      await required(pending[index + 2]).fulfill({
        status,
        body: index === 2 ? "null" : "<html>Proxy response</html>",
      });
      await check(page.getByRole("alert")).toContainText(
        status === 503
          ? "The usage service is unavailable. Try again."
          : "Usage response could not be read. Try again.",
      );
    }
    await page.getByRole("button", { name: "Retry usage" }).click();
    await check.poll(() => pending.length).toBe(6);
    await required(pending[5]).fulfill({ json: { ...recorded, run_id: "run-next" } });
    await check(page.locator(".usage-scope")).toHaveText("Up to event 69");
  } finally {
    await page.close();
  }
});
it("wraps long multilingual identities and large figures at the narrow desktop width", async () => {
  const data = structuredClone(early);
  const label = "LongModel".repeat(15) + " العربية 中文 🙂";
  data.teams = [{ ...required(data.teams[0]), agents: ["agent-1"] }];
  for (const cost of data.cost.rows) {
    cost.model = label + cost.agent_id;
    cost.known_usd = 987654321012345600;
  }
  data.cost.known_usd = 987654321012345600;
  for (const actor of data.hours.actors) actor.model = label + (actor.agent_id ?? "");
  const page = await open(data, { width: 1280 });
  try {
    await enter(page);
    await check(page.locator(".usage-closing")).toContainText("1 agent has worked");
    await check(page.locator(".usage-facts")).toContainText("US$987654321012345600.00");
    await check(page.locator("tbody")).toContainText(label);
    await page.getByRole("button", { name: "By model", exact: true }).click();
    expect(
      await page.locator(".usage-scroll").evaluate((node) => node.scrollWidth <= node.clientWidth),
    ).toBe(true);
    await page.getByRole("button", { name: "Maximize focus panel", exact: true }).click();
    expect(
      await page.locator(".usage-scroll").evaluate((node) => node.scrollWidth <= node.clientWidth),
    ).toBe(true);
  } finally {
    await page.close();
  }
});
it("renders incomplete and ranged values as lower bounds and quotes problems", async () => {
  const data = structuredClone(hosted);
  data.cost.coverage = "incomplete";
  data.hours.coverage = "incomplete";
  data.cost.problems = ["tokens are invalid"];
  data.hours.problems = ["time moved backwards"];
  data.cost.ranged_calls = 1;
  const page = await open(data);
  try {
    await enter(page);
    await check(page.locator(".usage-coverage.is-incomplete")).toHaveCount(2);
    await check(page.locator(".usage-facts")).toContainText("At least US$0.00184");
    await check(page.locator(".usage-facts")).toContainText("At least 33.0 s");
    await check(page.locator(".usage-facts")).toContainText("“tokens are invalid”");
    await check(page.locator(".usage-facts")).toContainText("“time moved backwards”");
    data.cost.coverage = "missing_metrics";
    data.cost.problems = ["1 call has unreported tokens"];
    data.cost.usd = null;
    await page.route("**/usage", (route) => route.fulfill({ json: data }));
    await refresh(page);
    await check(page.locator(".usage-facts")).toContainText("“1 call has unreported tokens”");
    await check(page.locator(".usage-facts")).not.toContainText("From");
  } finally {
    await page.close();
  }
});
it("keeps static replay without a Usage entry or request and preserves the phone notice", async () => {
  let requests = 0;
  const page = await open(recorded, {
    replay: true,
    handler: (route) => {
      requests++;
      return route.fulfill({ json: recorded });
    },
  });
  try {
    await camera(page);
    await check(page.getByRole("button", { name: "Usage", exact: true })).toHaveCount(0);
    expect(requests).toBe(0);
    for (const theme of ["dark", "day"]) {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      await check(page.getByText("This window is too narrow.", { exact: false })).toBeVisible();
      if (process.env.USAGE_SCREEN_SHOTS === "1") {
        await mkdir(directory, { recursive: true });
        await page.screenshot({ path: `${directory}phone-390-${theme}.png` });
      }
    }
  } finally {
    await page.close();
  }
});
it.each([1920, 1280])(
  "proves the seven approved desktop states at %ipx in both themes",
  async (width) => {
    const measurements: unknown[] = [];
    for (const theme of ["dark", "day"])
      for (const name of [
        "complete",
        "missing",
        "no-accepted",
        "by-agent",
        "by-team",
        "by-model",
        "by-role",
      ]) {
        const data =
          name === "complete"
            ? hosted
            : name === "no-accepted"
              ? early
              : name.startsWith("by-")
                ? lead
                : recorded;
        const page = await open(data, {
          width,
          theme,
          query: name === "missing" ? "recorded" : name === "no-accepted" ? "early" : "",
        });
        try {
          await enter(page);
          await check(page.locator(".usage-scope")).toContainText(String(data.seq));
          if (name.startsWith("by-")) {
            const button = page.getByRole("button", { name: `By ${name.slice(3)}`, exact: true });
            await button.focus();
            await page.keyboard.press("Enter");
            await check(button).toHaveAttribute("aria-pressed", "true");
            if (name === "by-agent") await check(row(page, "Reviews")).toContainText("——10.0 s");
            if (name === "by-team") {
              await check(row(page, "Lead team")).toContainText("At least 48.3 s");
              await check(row(page, "Total")).toHaveCount(0);
            }
            if (name === "by-model") {
              await check(row(page, "Muse Spark Open")).toContainText("At least 18.1 s");
              await check(row(page, "Muse Spark").first()).toContainText("At least 30.2 s");
            }
            if (name === "by-role")
              expect(await page.locator("tbody tr td:first-of-type").allTextContents()).toEqual([
                "19",
                "3",
                "2",
                "1",
              ]);
          } else {
            await check(page.locator(".usage-closing")).toContainText(
              name === "no-accepted" ? "32.7 s" : "33.0 s",
            );
            if (name === "complete") {
              await check(page.locator(".usage-facts")).toContainText("US$0.00184");
              await check(page.locator(".usage-facts")).toContainText("US$0.000918");
              await check(row(page, "Agent B")).toContainText("US$0.000859");
              await check(page.locator(".usage-grouping button")).toHaveCount(0);
            } else if (name === "missing") {
              await check(page.locator(".usage-facts")).toContainText("At least US$0.000978");
              await check(page.locator(".usage-facts")).toContainText("At least US$0.000489");
              await check(page.locator(".usage-facts")).toContainText("7 of 17 calls priced");
              await check(page.locator(".usage-scope")).toHaveText(
                "Whole recorded run, up to event 69",
              );
            } else {
              await check(page.getByText("Not available", { exact: true })).toBeVisible();
              await check(row(page, "Agent A")).toContainText("21.2 s so far");
            }
            if (width === 1280 && name !== "no-accepted") {
              const bottom = await page
                .locator(".usage-scope")
                .evaluate((node) => node.getBoundingClientRect().top);
              const b = await row(page, "Agent B").boundingBox();
              expect(required(b).y + required(b).height).toBeLessThanOrEqual(bottom + 1);
              const scroll = page.locator(".usage-scroll");
              await scroll.evaluate((node) => {
                node.scrollTop = node.scrollHeight;
              });
              expect(await scroll.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
              await scroll.evaluate((node) => {
                node.scrollTop = 0;
              });
            }
          }
          await page.evaluate(() => document.fonts.ready);
          if (name === "missing")
            measurements.push(
              await page.evaluate(() => {
                const element = (selector: string) => {
                  const node = document.querySelector(selector);
                  if (!node) throw new Error(selector);
                  return node;
                };
                const rect = (selector: string) => element(selector).getBoundingClientRect();
                return {
                  width: innerWidth,
                  theme: document.documentElement.dataset.theme,
                  panel: rect(".focus-panel").width,
                  inset: rect(".usage-closing").left - rect(".focus-panel").left,
                  closing: getComputedStyle(element(".usage-closing")).fontSize,
                  mono: getComputedStyle(element(".usage-closing .usage-number")).fontSize,
                  headerY: rect(".usage-panel thead").top,
                  footer: rect(".usage-scope").height,
                };
              }),
            );
          if (process.env.USAGE_SCREEN_SHOTS === "1") {
            await mkdir(directory, { recursive: true });
            await page.screenshot({ path: `${directory}${name}-${String(width)}-${theme}.png` });
          }
        } finally {
          await page.close();
        }
      }
    if (process.env.USAGE_SCREEN_SHOTS === "1")
      await writeFile(
        `${directory}measurements-${String(width)}.json`,
        JSON.stringify(measurements, null, 2),
      );
  },
  90_000,
);
