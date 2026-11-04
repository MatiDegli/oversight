import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";
import {
  acceptanceSupport,
  sizes,
  type AcceptanceFrame,
} from "./previewAcceptance.browser.support";

import { previewChatSupport, chatSizes } from "./previewChat.browser.support";
const chatOnly = process.env.OVERSIGHT_PREVIEW_CHAT_ONLY === "1";
const support = acceptanceSupport(!chatOnly);
const chatSupport = previewChatSupport(true, chatOnly);
it("captures 12 real-C form frames and six comparisons with unchanged focus and framing", async () => {
  const frames: AcceptanceFrame[] = [];
  for (const viewport of sizes)
    for (const theme of ["dark", "day"]) {
      const session = await support.session(viewport, theme);
      try {
        const page = await session.open();
        expect(await page.locator("iframe").count()).toBe(0);
        const frame = await support.ready(page);
        await check(page.getByRole("button", { name: "See preview", exact: true })).toBeFocused();
        await check(frame.locator("output")).toHaveAttribute("data-total-cents", "1500");
        const geometry = await page.locator(".preview-fixture-slot").boundingBox();
        const before = await support.capture(
          page,
          "before-DEMO10",
          viewport,
          theme,
          session.errors,
        );
        await frame.getByLabel("Coupon", { exact: true }).fill("DEMO10");
        await frame.getByRole("button", { name: "Update cart", exact: true }).click();
        await check(frame.locator("output")).toHaveAttribute("data-total-cents", "1350");
        expect(await frame.getByLabel(/Product 1/).inputValue()).toBe("1");
        expect(await frame.getByLabel("Coupon", { exact: true }).inputValue()).toBe("DEMO10");
        expect(await page.locator(".preview-fixture-slot").boundingBox()).toEqual(geometry);
        const after = await support.capture(page, "after-DEMO10", viewport, theme, session.errors);
        frames.push(before, after);
        await support.compare(before, after);
        expect(
          session.responses.some(
            (response) => response.method === "POST" && response.url.endsWith("/cart"),
          ),
        ).toBe(true);
        for (const response of session.responses) {
          expect(response.headers["set-cookie"]).toBeUndefined();
          expect(response.headers.location).toBeUndefined();
          expect(response.headers["cache-control"]).toBe("no-store");
        }
        expect(await session.context.cookies()).toEqual([]);
        await page.evaluate(() => window.previewAcceptance.close());
        await check(page.locator("iframe")).toHaveCount(0);
      } finally {
        await session.close();
      }
    }
  await support.manifest(frames);
}, 180_000);

it("submits quantities and confines scripts, navigation, popups, downloads and foreign forms", async () => {
  const session = await support.session();
  try {
    const page = await session.open();
    const app = await support.ready(page);
    await app.getByLabel(/Product 1/).fill("2");
    await app.getByLabel("Coupon", { exact: true }).fill("DEMO10");
    await app.getByRole("button", { name: "Update cart", exact: true }).click();
    await check(app.locator("output")).toHaveAttribute("data-total-cents", "2250");
    const frame = page.frames().find((item) => item.parentFrame());
    if (!frame) throw new Error("Missing application frame");
    const original = page.url();
    const requests: string[] = [];
    let popups = 0,
      downloads = 0;
    page.on("popup", () => {
      ++popups;
    });
    page.on("download", () => {
      ++downloads;
    });
    page.on("request", (request) => {
      if (request.url().includes("acceptance-blocked")) requests.push(request.url());
    });
    await frame.evaluate((base) => {
      const script = document.createElement("script");
      script.textContent = "document.body.dataset.scriptRan = 'yes'";
      document.body.append(script);
      for (const [label, target] of [
        ["Top escape", "_top"],
        ["Popup escape", "_blank"],
      ]) {
        const link = document.createElement("a");
        link.textContent = label ?? "";
        link.href = `${base}/acceptance-blocked`;
        link.target = target ?? "";
        document.body.append(link);
      }
      const download = document.createElement("a");
      download.textContent = "Download escape";
      download.href = "data:text/plain,blocked";
      download.download = "blocked.txt";
      document.body.append(download);
      const form = document.createElement("form");
      form.action = `${base}/acceptance-blocked`;
      form.method = "post";
      form.id = "foreign-form";
      document.body.append(form);
    }, support.url);
    for (const label of ["Top escape", "Popup escape", "Download escape"])
      await app.getByRole("link", { name: label }).click();
    await frame.evaluate(() => {
      (document.querySelector("#foreign-form") as HTMLFormElement).submit();
    });
    expect(await frame.locator("body").getAttribute("data-script-ran")).toBeNull();
    expect(page.url()).toBe(original);
    expect({ popups, downloads, requests }).toEqual({ popups: 0, downloads: 0, requests: [] });
    expect(session.security.length).toBeGreaterThanOrEqual(3);
    for (const origin of ["null", new URL(frame.url()).origin]) {
      const refused = await session.context.request.post(`${support.url}previews`, {
        headers: { Origin: origin },
        data: {},
      });
      expect(refused.status()).toBe(403);
      expect(((await refused.json()) as { error: { code: string } }).error.code).toBe("forbidden");
    }
    expect(session.errors).toEqual([]);
    await page.evaluate(() => window.previewAcceptance.close());
  } finally {
    await session.close();
  }
});

it("shows a status-only observer without a frame, renewal or takeover", async () => {
  const session = await support.session();
  try {
    const owner = await session.open();
    await support.ready(owner);
    const status = await owner.evaluate(() => window.previewAcceptance.snapshot().status);
    if (!status) throw new Error("Missing owner status");
    const observer = await session.open();
    const writes: string[] = [];
    observer.on("request", (request) => {
      if (request.method() === "POST") writes.push(request.url());
    });
    await observer.evaluate((value) => {
      window.previewAcceptance.observe(value);
    }, status);
    await check(observer.getByText("Preview in use in another tab", { exact: true })).toBeVisible();
    await observer.getByRole("button", { name: "See preview", exact: true }).click();
    await check(observer.locator("iframe")).toHaveCount(0);
    await observer.getByRole("button", { name: "Close", exact: true }).click();
    expect(writes).toEqual([]);
    const exposed = await owner.evaluate(() =>
      JSON.stringify({
        snapshot: window.previewAcceptance.snapshot(),
        local: Object.fromEntries(
          Object.keys(localStorage).map((key) => [key, localStorage.getItem(key)]),
        ),
        session: Object.fromEntries(
          Object.keys(sessionStorage).map((key) => [key, sessionStorage.getItem(key)]),
        ),
        html: document.body.innerHTML,
        url: location.href,
      }),
    );
    expect(exposed).not.toContain("lease_token");
    await owner.evaluate(() => window.previewAcceptance.close());
  } finally {
    await session.close();
  }
});

it("loses the lease on reload and waits for expiry when pagehide Stop delivery is lost", async () => {
  const session = await support.session();
  try {
    const page = await session.open();
    await support.ready(page);
    const status = await page.evaluate(() => window.previewAcceptance.snapshot().status);
    if (!status) throw new Error("Missing preview status");
    await session.context.route("**/previews/*/stop", (route) => route.abort("failed"));
    await page.reload();
    await page.waitForFunction(() => Boolean(window.previewAcceptance), undefined, {
      timeout: 10_000,
    });
    expect(await page.evaluate(() => window.previewAcceptance.snapshot().owned)).toBe(false);
    await page.evaluate((value) => {
      window.previewAcceptance.observe(value);
    }, status);
    await check(page.getByText("Preview in use in another tab", { exact: true })).toBeVisible();
    await check(page.locator("iframe")).toHaveCount(0);
    await check
      .poll(
        async () => {
          const response = await session.context.request.get(
            `${support.url}previews/${status.preview_id}`,
          );
          const result = (await response.json()) as {
            status: { state: string; reason: string | null };
          };
          return result.status;
        },
        { timeout: 40_000, intervals: [500] },
      )
      .toMatchObject({ state: "stopped", reason: "lease_expired" });
    expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
    expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  } finally {
    await session.close();
  }
}, 55_000);

it("keeps failed network admission private and suppresses rapid duplicate starts", async () => {
  const session = await support.session();
  try {
    const page = await session.open();
    const starts: string[] = [];
    await session.context.route("**/previews", async (route) => {
      starts.push(route.request().method());
      await route.fulfill({
        status: 503,
        headers: { "Cache-Control": "no-store" },
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "PRIVATE_BODY_NEVER_DISPLAY" } }),
      });
    });
    await page.evaluate(async () => {
      await Promise.all(Array.from({ length: 10 }, () => window.previewAcceptance.see()));
    });
    expect(starts).toEqual(["POST"]);
    await check(page.getByRole("alert")).toContainText("The preview could not be confirmed.");
    await check(page.locator("iframe")).toHaveCount(0);
    expect(await page.locator("body").innerText()).not.toContain("PRIVATE_BODY_NEVER_DISPLAY");
    await page.getByRole("button", { name: "Close", exact: true }).press("Enter");
    await session.context.setOffline(true);
    await page.getByRole("button", { name: "See preview", exact: true }).press("Enter");
    await check(page.getByRole("alert")).toContainText("Replacement is unavailable");
    expect(starts).toEqual(["POST"]);
    await session.context.setOffline(false);
    expect(session.errors).toEqual([
      "Failed to load resource: the server responded with a status of 503 (Service Unavailable)",
    ]);
  } finally {
    await session.close();
  }
});

it.each(["dark", "day"])(
  "wraps mixed-script observer metadata and retains keyboard focus in %s",
  async (theme) => {
    const session = await support.session(sizes[2], theme);
    try {
      const page = await session.open();
      await page.evaluate((candidate) => {
        if (!candidate) throw new Error("Missing candidate");
        const text = "العربية 漢字 😀 ".repeat(12);
        window.previewAcceptance.observe({
          ...candidate,
          preview_id: `preview-${"a".repeat(32)}`,
          change_id: text,
          agent_id: text,
          head_commit: "a".repeat(200),
          state: "ready",
          reason: null,
        });
      }, support.candidate);
      await check(page.getByRole("status")).toContainText("Preview in use in another tab");
      await page.keyboard.press("Tab");
      await check(page.getByRole("button", { name: "See preview", exact: true })).toBeFocused();
      await page.keyboard.press("Tab");
      const close = page.getByRole("button", { name: "Close", exact: true });
      await check(close).toBeFocused();
      await page.emulateMedia({ forcedColors: "active" });
      expect(await close.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe(
        "solid",
      );
      await page.evaluate(() => {
        const elements = [...document.querySelectorAll<HTMLElement>(".preview-panel *")];
        const sizes = elements.map((element) => parseFloat(getComputedStyle(element).fontSize));
        elements.forEach((element, index) => {
          element.style.fontSize = `${String((sizes[index] ?? 16) * 2)}px`;
        });
      });
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(
        false,
      );
      await check(page.locator("iframe")).toHaveCount(0);
      await close.press("Enter");
      await check(page.getByRole("complementary")).toHaveCount(0);
      expect(session.errors).toEqual([]);
    } finally {
      await session.close();
    }
  },
);

it.runIf(chatOnly)(
  "App Chat journey: production click to real-C shop, form, lease, review and authenticated close",
  async () => {
    for (const viewport of chatSizes)
      for (const theme of ["dark", "day"]) {
        const session = await chatSupport.session(viewport, theme);
        const { page } = session;
        try {
          await page
            .getByRole("button", { name: "Preview app", exact: true })
            .scrollIntoViewIfNeeded();
          await check(
            page.getByRole("button", { name: "Review change", exact: true }),
          ).toBeInViewport();
          await chatSupport.capture(session, "option");
          await check(page.locator("iframe")).toHaveCount(0);
          await page.getByRole("button", { name: "Preview app", exact: true }).click();
          const app = page.frameLocator("iframe");
          await check(app.getByRole("heading", { name: "Candidate shop" })).toBeVisible({
            timeout: 20_000,
          });
          await check(page.locator("iframe")).toHaveAttribute(
            "sandbox",
            "allow-scripts allow-same-origin allow-forms",
          );
          await check(page.locator("iframe")).toHaveAttribute("referrerpolicy", "no-referrer");
          await check(app.locator("output")).toHaveAttribute("data-total-cents", "1500");
          await chatSupport.capture(session, "ready");
          await chatSupport.capture(session, "before-form");
          await app.getByLabel("Coupon", { exact: true }).fill("DEMO10");
          await app.getByRole("button", { name: "Update cart", exact: true }).click();
          await check(app.locator("output")).toHaveAttribute("data-total-cents", "1350");
          await chatSupport.capture(session, "after-coupon");
          await app.getByLabel(/Product 1/).fill("2");
          await app.getByRole("button", { name: "Update cart", exact: true }).click();
          await check(app.locator("output")).toHaveAttribute("data-total-cents", "2250");
          await chatSupport.capture(session, "after-quantity");
          await check.poll(() => session.calls.renew, { timeout: 15_000 }).toBeGreaterThan(0);
          expect(session.calls.form).toBe(2);
          expect(await session.context.cookies()).toEqual([]);
          await page.locator(".preview-panel button").click();
          await check(page.locator(".chat-preview-slot")).toHaveCount(0);
          expect(session.calls.stop).toBeGreaterThan(0);
          await check(page.locator("iframe")).toHaveCount(0);
          await chatSupport.capture(session, "closed");
          await page.getByRole("button", { name: "Review change", exact: true }).click();
          await check(page.getByRole("complementary", { name: "Review panel" })).toBeVisible();
          expect(session.pageErrors).toEqual([]);
        } finally {
          await session.close();
        }
      }
  },
  210_000,
);
