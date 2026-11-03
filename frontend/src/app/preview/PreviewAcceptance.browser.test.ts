import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";
import {
  acceptanceSupport,
  sizes,
  type AcceptanceFrame,
} from "./previewAcceptance.browser.support";

const support = acceptanceSupport();
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
