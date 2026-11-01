import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";

let browser: Browser;
const servers: ViteDevServer[] = [];
const entries: { url: string; html: string }[] = [];
const refusal = "Rules mode is closed until Revert, protected paths and follow-main land";
beforeAll(async () => {
  for (const mode of ["development", "replay"]) {
    const server = await createServer({
      root: fileURLToPath(new URL("../../", import.meta.url)),
      mode,
      plugins: [
        {
          name: "settings-connected-events",
          configureServer(server) {
            server.middlewares.use("/events", (_request, response) => {
              response.writeHead(200, {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
              });
              // Keep the idle stream open until the browser closes the page.
              response.write(": connected\n\n");
            });
          },
        },
      ],
      cacheDir: fileURLToPath(new URL(`../../../.shots/rules-${mode}-cache`, import.meta.url)),
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    servers.push(server);
    const html = await server.transformIndexHtml(
      "/",
      `<!doctype html><html><body><div id="root"></div><script type="module">import "/src/theme/index.css"; import "/src/app/settings.browser.fixture.tsx";</script></body></html>`,
    );
    await server.listen();
    entries.push({ url: server.resolvedUrls?.local[0] ?? "", html });
  }
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await Promise.all(servers.map((s) => s.close()));
});

async function open(replay = false, width = 1920, height = 1080, theme = "dark") {
  const entry = entries[replay ? 1 : 0];
  if (!entry) throw new Error("Missing server");
  const page = await browser.newPage({ viewport: { width, height } });
  await page.route("**/", (r) => r.fulfill({ contentType: "text/html", body: entry.html }));
  await page.goto(entry.url);
  await check(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible({
    timeout: 15_000,
  });
  await page.evaluate((theme) => {
    document.documentElement.dataset.theme = theme;
  }, theme);
  return page;
}
async function mode(page: Page, mode: "ask_me" | "merge_when_safe", revision: number) {
  await page.evaluate(
    ({ mode, revision }) => {
      const store = (window as unknown as { appStore: typeof import("../state").stateStore })
        .appStore;
      const state = store.getState().state;
      store.getState().applyEvents([
        {
          seq: state.lastSeq + 1,
          ts: state.clock ?? "2026-10-31T17:00:00Z",
          type: "merge_mode.changed",
          payload: { mode, revision },
        },
      ]);
    },
    { mode, revision },
  );
}
const rules = (page: Page) =>
  page.getByRole("button", { name: "Merge alone when safe", exact: false });
async function disabledCards(page: Page) {
  const cards = page.getByRole("region", { name: "Merge mode", exact: true }).getByRole("button");
  for (const card of await cards.all()) {
    await check(card).toBeDisabled();
    await page.mouse.move(0, 0);
    const before = await card.evaluate((element) => getComputedStyle(element).borderColor);
    await card.hover({ force: true });
    const styles = await card.evaluate((element) => {
      const text = element.lastElementChild;
      if (!text) throw new Error("Missing card text");
      return {
        border: getComputedStyle(element).borderColor,
        cursor: getComputedStyle(element).cursor,
        colors: [...text.children].map((child) => getComputedStyle(child).color),
        faint: getComputedStyle(element).getPropertyValue("--faint").trim(),
      };
    });
    expect(styles.border).toBe(before);
    expect(styles.cursor).toBe("default");
    // Resolve the token through the browser, including each theme's color format.
    const faint = await card.evaluate((element, color) => {
      const probe = document.createElement("span");
      probe.style.color = color;
      element.append(probe);
      const resolved = getComputedStyle(probe).color;
      probe.remove();
      return resolved;
    }, styles.faint);
    expect(styles.colors).toEqual([faint, faint]);
  }
}
async function shot(page: Page, name: string, suffix: string) {
  const status = page.getByLabel("Status line");
  await check(status).not.toContainText("Connection lost");
  await check(status).not.toContainText("Live event stream disconnected");
  await check(status.getByRole("button", { name: "Retry connection" })).toHaveCount(0);
  if (name !== "mode-settings-replay") await check(status).toContainText("Connected");
  if (process.env.RULES_UI_SHOTS !== "1") return;
  const directory = fileURLToPath(new URL("../../../.shots/rules-ui/", import.meta.url));
  await mkdir(directory, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${directory}${name}-${suffix}.png` });
}

it("opens Settings only on demand, preserves focus, and sends one observed-revision PUT", async () => {
  const page = await open();
  page.setDefaultTimeout(5000);
  const requests: unknown[] = [];
  await page.route("**/merge-mode", (r) => {
    requests.push(r.request().postDataJSON());
    expect(r.request().method()).toBe("PUT");
    return r.fulfill({ status: 409, json: { error: { code: "wrong_state", message: refusal } } });
  });
  try {
    const gear = page.getByRole("button", { name: "Settings", exact: true });
    await gear.focus();
    await mode(page, "ask_me", 3);
    await check(gear).toBeFocused();
    await check(page.getByRole("tab", { name: "Settings", exact: true })).toHaveCount(0);
    await gear.click();
    await check(page.getByRole("tab", { name: "Settings", exact: true })).toHaveCount(1);
    const ask = page.getByRole("button", { name: "Ask me Every change", exact: false });
    await ask.click();
    await ask.focus();
    await page.keyboard.press("Tab");
    await check(rules(page)).toBeFocused();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");
    await rules(page).dispatchEvent("click", { detail: 0 });
    expect(requests).toEqual([]);
    await rules(page).click();
    await check(page.getByRole("alert").filter({ hasText: refusal })).toBeVisible();
    await check(rules(page)).toBeFocused();
    expect(requests).toEqual([{ mode: "merge_when_safe", revision: 3 }]);
    await page.getByRole("button", { name: /Open decisions inbox/ }).click();
    await gear.click();
    await check(page.getByRole("alert").filter({ hasText: refusal })).toBeVisible();
    await mode(page, "merge_when_safe", 4);
    await check(page.getByRole("alert").filter({ hasText: refusal })).toHaveCount(0);
    await page.getByRole("button", { name: "Close tab Settings", exact: true }).click();
    await page.getByRole("button", { name: "Close tab Decisions", exact: true }).click();
    await page.getByRole("button", { name: "Review change", exact: true }).click();
    await check(page.getByRole("button", { name: "Approve and merge", exact: true })).toBeVisible();
    expect(requests).toHaveLength(1);
  } finally {
    await page.close();
  }
});

it("waits for the event revision after a 200 and never marks the response optimistically", async () => {
  const page = await open();
  await page.route("**/merge-mode", (r) =>
    r.fulfill({ json: { mode: "merge_when_safe", revision: 1 } }),
  );
  try {
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await rules(page).click();
    await check(rules(page)).toBeDisabled();
    await check(rules(page)).toHaveAttribute("aria-pressed", "false");
    await mode(page, "merge_when_safe", 1);
    await check(rules(page)).toBeEnabled();
    await check(rules(page)).toHaveAttribute("aria-pressed", "true");
  } finally {
    await page.close();
  }
});

it("keeps a long multilingual refusal readable with 200% text and vertical scrolling", async () => {
  const page = await open(false, 1280, 800);
  const message = ("رفض🙂漢字é" + "x".repeat(120)).repeat(16);
  await page.route("**/merge-mode", (r) =>
    r.fulfill({ status: 409, json: { error: { code: "wrong_state", message } } }),
  );
  try {
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "200%";
    });
    await rules(page).click();
    await check(page.getByRole("alert").filter({ hasText: message })).toHaveText(message);
    const section = page.getByRole("region", { name: "Merge mode", exact: true });
    const geometry = await section.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
      const [first, second] = [...element.querySelectorAll("button")].map((card) =>
        card.getBoundingClientRect(),
      );
      const alert = element.querySelector('[role="alert"]');
      if (!first || !second || !alert) throw new Error("Missing merge mode controls or refusal");
      return {
        overflow: element.scrollWidth - element.clientWidth,
        scrolled: element.scrollTop,
        end: alert.getBoundingClientRect().bottom,
        bottom: element.getBoundingClientRect().bottom,
        stacked: second.top >= first.bottom,
      };
    });
    expect(geometry.overflow).toBeLessThanOrEqual(1);
    expect(geometry.scrolled).toBeGreaterThan(0);
    expect(geometry.end).toBeLessThanOrEqual(geometry.bottom);
    expect(geometry.stacked).toBe(true);
  } finally {
    await page.close();
  }
});

it.each(["network", "server"])(
  "recovers from a %s failure only on the next pointer click",
  async (failure) => {
    const page = await open();
    let requests = 0;
    await page.route("**/merge-mode", (r) => {
      requests += 1;
      return failure === "network" ? r.abort("internetdisconnected") : r.fulfill({ status: 500 });
    });
    try {
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await rules(page).click();
      const message =
        failure === "network"
          ? "Setting the merge mode did not reach the server. Check the connection and try again."
          : "Setting the merge mode failed: the server hit an internal error. Try again.";
      await check(page.getByRole("alert").filter({ hasText: message })).toBeVisible();
      await check(rules(page)).toBeEnabled();
      expect(requests).toBe(1);
      await rules(page).click();
      await check(page.getByRole("alert").filter({ hasText: message })).toBeVisible();
      expect(requests).toBe(2);
    } finally {
      await page.close();
    }
  },
);

it.each([
  [1920, 1080],
  [1280, 800],
])(
  "captures approved states at %ix%i in both themes",
  async (width, height) => {
    for (const theme of ["dark", "day"]) {
      const page = await open(false, width, height, theme);
      const suffix = `${String(width)}x${String(height)}-${theme}`;
      try {
        await check(page.getByLabel("Status line")).toContainText("Merge mode: Ask me");
        await shot(page, "mode-status-ask", suffix);
        await mode(page, "merge_when_safe", 1);
        await check(page.getByLabel("Status line")).toContainText(
          "Merge mode: Merge alone when safe",
        );
        await shot(page, "mode-status-rules", suffix);
        await mode(page, "ask_me", 2);
        await page.getByRole("button", { name: "Settings", exact: true }).click();
        await shot(page, "mode-settings-ask", suffix);
        const request = page.waitForRequest("**/merge-mode");
        let release: (() => void) | undefined;
        let requests = 0;
        await page.route("**/merge-mode", async (r) => {
          requests += 1;
          await new Promise<void>((resolve) => {
            release = resolve;
          });
          await r.fulfill({
            status: 409,
            json: { error: { code: "wrong_state", message: refusal } },
          });
        });
        await rules(page).click();
        await request;
        await check(rules(page)).toBeDisabled();
        await check(rules(page)).toBeFocused();
        // aria-disabled retains focus and still rejects additional pointer clicks.
        await rules(page).dispatchEvent("click", { detail: 1 });
        expect(requests).toBe(1);
        await disabledCards(page);
        await shot(page, "mode-settings-pending", suffix);
        release?.();
        await check(page.getByRole("alert").filter({ hasText: refusal })).toBeVisible();
        await check(rules(page)).toBeFocused();
        await shot(page, "mode-settings-refused", suffix);
      } finally {
        await page.close();
      }
      const replay = await open(true, width, height, theme);
      const requests: string[] = [];
      replay.on("request", (r) => {
        if (r.url().endsWith("/merge-mode")) requests.push(r.method());
      });
      try {
        await replay.getByRole("button", { name: "Settings", exact: true }).click();
        await check(
          replay.getByText("Setting the merge mode is not available in a replay."),
        ).toBeVisible();
        await disabledCards(replay);
        await rules(replay).click({ force: true });
        await rules(replay).focus();
        await replay.keyboard.press("Enter");
        await replay.keyboard.press("Space");
        expect(requests).toEqual([]);
        await shot(replay, "mode-settings-replay", suffix);
      } finally {
        await replay.close();
      }
    }
  },
  60_000,
);
