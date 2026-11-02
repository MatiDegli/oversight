import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Locator, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./composerTabs.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/composer-tabs-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/composerTabs.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it("restores a draft after a tab unmount and confirms a successful pause", async () => {
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    reducedMotion: "reduce",
  });
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.composerTabsTest);
    await page.evaluate(() => {
      window.composerTabsTest.mount();
    });
    const message = page.getByRole("textbox", { name: "Message Agent A", exact: true });
    const draft = "  Keep this unsent draft.\nAnd this line.  ";
    await message.fill(draft);
    const original = await message.elementHandle();

    const decisions = page.getByRole("tab", { name: "Decisions", exact: true });
    await decisions.click();
    await check(decisions).toHaveAttribute("aria-selected", "true");
    await check(page.locator("textarea")).toHaveCount(0);
    expect(await original.evaluate((element) => element.isConnected)).toBe(false);

    const conversation = page.getByRole("tab", { name: /Agent A · conversation/ });
    await conversation.click();
    await check(conversation).toHaveAttribute("aria-selected", "true");
    await check(message).toBeVisible();
    await check(message).toHaveValue(draft);
    const returned = await message.elementHandle();
    expect(await returned.evaluate((element, previous) => element === previous, original)).toBe(
      false,
    );
    expect(await original.evaluate((element) => element.isConnected)).toBe(false);

    await page.getByRole("button", { name: "Pause", exact: true }).click();
    const pausing = page.getByRole("button", { name: "Pausing after this step", exact: true });
    await check(pausing).toBeVisible();
    await check(pausing).toBeDisabled();
    expect(await page.evaluate(() => window.composerTabsTest.calls())).toEqual([
      { command: "pause", id: "agent-1" },
    ]);
  } finally {
    await page.close();
  }
});

const failedDraft = "  Check pricing before the next step.\nKeep this draft exactly.  ";
const sendFailure = "Send did not reach the server. Check the connection and try again.";

async function mountDeferred(page: Page, theme: "dark" | "day" = "dark") {
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.composerTabsTest);
  await page.evaluate((theme) => {
    window.composerTabsTest.mount({ deferSend: true, theme });
  }, theme);
}

function composer(page: Page) {
  return {
    message: page.getByRole("textbox", { name: "Message Agent A", exact: true }),
    send: page.getByRole("button", { name: "Send", exact: true }),
    alert: page.getByRole("alert"),
    decisions: page.getByRole("tab", { name: "Decisions", exact: true }),
    conversation: page.getByRole("tab", { name: /Agent A · conversation/ }),
  };
}

async function expectFailure(page: Page, draft: string) {
  const { message, send, alert } = composer(page);
  await check(message).toHaveValue(draft);
  await check(message).toBeEditable();
  await check(send).toHaveAttribute("aria-disabled", "false");
  await check(alert).toHaveText(sendFailure);
  await check(alert).toHaveAttribute("title", sendFailure);
}

async function startSend(page: Page, draft: string) {
  const { message, send, alert } = composer(page);
  await message.fill(draft);
  await send.click();
  await check(message).toHaveJSProperty("readOnly", true);
  await check(send).toHaveAttribute("aria-disabled", "true");
  await check(alert).toHaveCount(0);
}

async function settleSend(page: Page, outcome: "resolve" | "reject", focus: Locator) {
  await check(focus).toBeFocused();
  await page.evaluate((outcome) => {
    window.composerTabsTest.settleSend(outcome);
  }, outcome);
  await check(focus).toBeFocused();
}

async function tabRoundTrip(page: Page, whileUnmounted?: () => Promise<void>) {
  const { message, decisions, conversation } = composer(page);
  const original = await message.elementHandle();
  await decisions.click();
  await check(decisions).toHaveAttribute("aria-selected", "true");
  await check(page.locator("textarea")).toHaveCount(0);
  expect(await original.evaluate((element) => element.isConnected)).toBe(false);
  await whileUnmounted?.();
  await conversation.click();
  await check(conversation).toHaveAttribute("aria-selected", "true");
  await check(message).toBeVisible();
  const returned = await message.elementHandle();
  expect(await returned.evaluate((element, previous) => element === previous, original)).toBe(
    false,
  );
  expect(await original.evaluate((element) => element.isConnected)).toBe(false);
  await check(conversation).toBeFocused();
}

async function expectCalls(page: Page, texts: string[], paused = false) {
  expect(await page.evaluate(() => window.composerTabsTest.calls())).toEqual([
    ...texts.map((text) => ({ command: "message", id: "agent-1", text })),
    ...(paused ? [{ command: "pause", id: "agent-1" }] : []),
  ]);
}

async function lateRejection(page: Page, draft: string) {
  await startSend(page, draft);
  await tabRoundTrip(page, async () => {
    await settleSend(page, "reject", composer(page).decisions);
    await check(page.locator("textarea")).toHaveCount(0);
  });
  await expectFailure(page, draft);
  await check(composer(page).conversation).toBeFocused();
}

async function failedSendBehavior() {
  const page = await browser.newPage({ reducedMotion: "reduce" });
  try {
    await mountDeferred(page);
    // Exercise the late-settlement race first so the regression fails at that boundary.
    await lateRejection(page, failedDraft);
    await expectCalls(page, [failedDraft]);
    const { message, send, alert } = composer(page);
    const edited = `${failedDraft}\nAn edit keeps the explanation.`;
    await message.fill(edited);
    await expectFailure(page, edited);
    await startSend(page, edited);
    await expectCalls(page, [failedDraft, edited]);
    await settleSend(page, "resolve", send);
    await check(message).toHaveValue("");
    await check(message).toBeEditable();
    await check(alert).toHaveCount(0);
    await check(send).toBeFocused();

    await startSend(page, failedDraft);
    await settleSend(page, "reject", send);
    await expectFailure(page, failedDraft);
    await check(send).toBeFocused();
    await tabRoundTrip(page);
    await expectFailure(page, failedDraft);
    await expectCalls(page, [failedDraft, edited, failedDraft]);
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await check(alert).toHaveCount(0);
    await check(message).toHaveValue(failedDraft);
    const pausing = page.getByRole("button", { name: "Pausing after this step", exact: true });
    await check(pausing).toBeVisible();
    await check(pausing).toBeDisabled();
    await expectCalls(page, [failedDraft, edited, failedDraft], true);
  } finally {
    await page.close();
  }
}

function failedSendCase(capture?: () => Promise<void>) {
  it("retains failed sends across real tab unmounts, including late rejection", async () => {
    await failedSendBehavior();
    await capture?.();
  }, 60_000);
}

// Register the behavior once in either mode; captures extend the same case.
if (process.env.SEND_FAILURE_SHOTS !== "1") failedSendCase();

const shots = fileURLToPath(new URL("../../../.shots/send-failure/", import.meta.url));
async function settledShot(page: Page, path: string) {
  await check(composer(page).message).toBeInViewport();
  await check(composer(page).alert).toBeInViewport();
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          resolve();
        }),
      ),
    );
  });
  await page.screenshot({ path, fullPage: false });
}

describe.skipIf(process.env.SEND_FAILURE_SHOTS !== "1")("failed send captures", () => {
  if (process.env.SEND_FAILURE_SHOTS !== "1") return;
  failedSendCase(async () => {
    await mkdir(shots, { recursive: true });
    for (const [width, height] of [
      [1280, 800],
      [1920, 1080],
    ] as const) {
      for (const theme of ["dark", "day"] as const) {
        const page = await browser.newPage({
          viewport: { width, height },
          reducedMotion: "reduce",
        });
        try {
          await mountDeferred(page, theme);
          const { send } = composer(page);
          await startSend(page, failedDraft);
          await settleSend(page, "reject", send);
          await expectFailure(page, failedDraft);
          await settledShot(page, `${shots}failed-before-tab-${String(width)}-${theme}.png`);
          await tabRoundTrip(page);
          await expectFailure(page, failedDraft);
          await settledShot(page, `${shots}failed-after-tab-${String(width)}-${theme}.png`);
          await lateRejection(page, failedDraft);
          await expectCalls(page, [failedDraft, failedDraft]);
        } finally {
          await page.close();
        }
      }
    }
  });
});
