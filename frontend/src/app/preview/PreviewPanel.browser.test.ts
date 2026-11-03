import { expect as check, type Page } from "@playwright/test";
import { expect, it } from "vitest";
import { deferred, fakeCandidate } from "./previewFake";
import { previewReasonCopy } from "./previewReasons";
import { previewReasons } from "./previewTypes";
import { previewBrowserSupport, scenarios } from "./previewPanel.browser.support";
import type { PreviewSurface } from "./previewOpening";

const support = previewBrowserSupport("behavior");
async function noErrors(page: Page) {
  expect(await support.metrics(page)).toEqual({ pageErrors: [], overflow: { page: 0, panel: 0 } });
}
it.each(scenarios)("mounts %s with fixed copy and persistent Close", async (scenario) => {
  const session = await support.session({ scenario });
  try {
    const page = await session.open();
    await check(page.getByRole("button", { name: "Close", exact: true })).toBeVisible();
    const reason = previewReasons.find(
      (reason) => scenario === `failed-${reason}` || scenario === `stopped-${reason}`,
    );
    if (reason) await check(page.getByRole("status")).toContainText(previewReasonCopy[reason]);
    if (scenario === "isolation-before-launch") {
      await check(page.getByRole("status")).toContainText(
        "Live preview unavailable: isolation C is unavailable for this session.",
      );
      expect(session.authority.calls).toHaveLength(0);
    }
    if (scenario === "observer") {
      await check(page.getByRole("status")).toContainText("Preview in use in another tab");
      await check(page.locator("iframe")).toHaveCount(0);
    }
    if (scenario === "stopping-closed") {
      await check(page.locator(".preview-panel-state")).toHaveText("Stopping");
      await check(page.getByRole("status")).toContainText("Waiting for verified closure.");
    }
    await noErrors(page);
  } finally {
    await session.close();
  }
});
it("isolates two tabs: observer receives public status only and Close is local", async () => {
  const session = await support.session();
  try {
    const owner = await session.open();
    const observer = await session.open("panel", "", "observer");
    await check(owner.locator("iframe")).toHaveAttribute("sandbox", "allow-forms");
    await check(owner.locator("iframe")).toHaveAttribute("referrerpolicy", "no-referrer");
    await check(owner.locator("iframe")).toHaveAttribute("title", "Live preview for agent-a");
    await check(observer.getByText("Preview in use in another tab", { exact: true })).toBeVisible();
    await check(observer.locator("iframe")).toHaveCount(0);
    await check(observer.getByRole("button")).toHaveCount(1);
    await observer.evaluate(() => {
      window.previewFixture.advance(20_000);
    });
    const before = session.authority.calls.length;
    await observer.getByRole("button", { name: "Close", exact: true }).press("Enter");
    expect(session.authority.calls).toHaveLength(before);
    await observer.evaluate(() => window.previewFixture.see({ agent_id: "agent-b" }));
    expect(session.authority.calls).toHaveLength(before);
    await check(observer.getByText("Preview in use in another tab", { exact: true })).toBeVisible();
    for (const page of [owner, observer]) {
      const exposed = await page.evaluate(() => ({
        snapshot: window.previewFixture.snapshot(),
        local: Object.fromEntries(
          Object.keys(localStorage).map((key) => [key, localStorage.getItem(key)]),
        ),
        session: Object.fromEntries(
          Object.keys(sessionStorage).map((key) => [key, sessionStorage.getItem(key)]),
        ),
        html: document.body.innerHTML,
        fixture: JSON.stringify(window.previewFixture),
        url: location.href,
      }));
      expect(JSON.stringify(exposed)).not.toMatch(/lease_token|fake-private/);
      await noErrors(page);
    }
  } finally {
    await session.close();
  }
});
it("replacement waits for verified drainage, reads fresh head and prevents repeated starts", async () => {
  const session = await support.session();
  try {
    const page = await session.open();
    session.authority.gate = deferred<undefined>();
    await page.evaluate(() => {
      void window.previewFixture.see({ agent_id: "agent-b" });
    });
    await check
      .poll(() => session.authority.calls.filter((call) => call.kind === "stop").length)
      .toBe(1);
    await page.evaluate(() => {
      void window.previewFixture.see();
      window.previewFixture.candidate({
        run_id: "run-preview",
        agent_id: "agent-b",
        revision: 3,
        head_commit: "fresh-head",
      });
    });
    expect(session.authority.calls.filter((call) => call.kind === "start")).toHaveLength(1);
    session.authority.gate.resolve(undefined);
    await check
      .poll(() => session.authority.calls.filter((call) => call.kind === "start").length)
      .toBe(2);
    expect(session.authority.calls.filter((call) => call.kind === "start")[1]?.candidate).toEqual({
      ...fakeCandidate,
      agent_id: "agent-b",
      revision: 3,
      head_commit: "fresh-head",
    });
    await check(page.locator("iframe")).toHaveAttribute("title", "Live preview for agent-b");
    await noErrors(page);
  } finally {
    await session.close();
  }
});
it("keeps observer Close dismissed through background updates and a new owner's preview", async () => {
  const session = await support.session();
  try {
    const owner = await session.open();
    const observer = await session.open("panel", "", "observer");
    await observer.getByRole("button", { name: "Close", exact: true }).click();
    await owner.evaluate(() => window.previewFixture.publish("stopping", "closed"));
    await observer.evaluate(() => window.previewFixture.observePublic());
    await check(observer.getByRole("complementary", { name: "Live preview panel" })).toHaveCount(0);
    await owner.evaluate(() => window.previewFixture.see({ agent_id: "agent-b" }));
    await observer.evaluate(() => window.previewFixture.observePublic());
    await check(observer.getByRole("complementary", { name: "Live preview panel" })).toHaveCount(0);
    const before = session.authority.calls.length;
    await observer.evaluate(() => window.previewFixture.see());
    await check(observer.getByText("Preview in use in another tab", { exact: true })).toBeVisible();
    expect(session.authority.calls).toHaveLength(before);
    await noErrors(owner);
    await noErrors(observer);
  } finally {
    await session.close();
  }
});
it("recovers a rejected agent-b start while agent-a owns the slot", async () => {
  const session = await support.session();
  try {
    const owner = await session.open();
    const page = await session.open("panel", "agent=agent-b"); // Start races public observation.
    expect(await page.evaluate(() => window.previewFixture.snapshot().notice)).toBe("start_failed");
    const before = session.authority.calls.length;
    await page.getByRole("button", { name: "Close", exact: true }).press("Enter");
    await check(page.getByRole("complementary", { name: "Live preview panel" })).toHaveCount(0);
    await page.getByRole("button", { name: "See preview", exact: true }).click();
    await check(page.getByRole("alert")).toContainText("The preview could not be confirmed.");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await page.evaluate(() => window.previewFixture.observePublic());
    await check(page.getByRole("complementary", { name: "Live preview panel" })).toHaveCount(0);
    expect(session.authority.calls).toHaveLength(before);
    await page.getByRole("button", { name: "See preview", exact: true }).click();
    await check(page.getByRole("alert")).toContainText("The preview could not be confirmed.");
    await check(page.getByText("Preview in use in another tab", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await page.evaluate(() => {
      window.previewFixture.advance(5_000);
    });
    await page.evaluate(() => window.previewFixture.observePublic());
    expect(await page.evaluate(() => window.previewFixture.snapshot().uncertain)).toBe(false);
    session.clock.advance(30_001);
    await page.evaluate(() => window.previewFixture.observePublic());
    await check(page.getByRole("complementary", { name: "Live preview panel" })).toHaveCount(0);
    await page.getByRole("button", { name: "See preview", exact: true }).click();
    await check(page.locator("iframe")).toHaveAttribute("title", "Live preview for agent-b");
    expect(session.authority.calls.filter((call) => call.kind === "start")).toHaveLength(2);
    await noErrors(owner);
    await noErrors(page);
  } finally {
    await session.close();
  }
});
it.each(["reject", "unverified"] as const)(
  "recovers from %s stop after server expiry and retries Close without an expired token",
  async (mode) => {
    const session = await support.session();
    try {
      const page = await session.open();
      session.authority.stopMode = mode;
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await check(page.getByRole("alert")).toContainText("Closure is unconfirmed");
      expect(await page.evaluate(() => window.previewFixture.snapshot().status?.state)).toBe(
        "ready",
      );
      await check(page.locator("iframe")).toBeVisible();
      await page.evaluate(() => window.previewFixture.observePublic());
      session.clock.advance(30_001);
      await page.evaluate(() => window.previewFixture.observePublic());
      await check(page.getByRole("status")).toContainText(previewReasonCopy.lease_expired);
      await check(page.locator("iframe")).toHaveCount(0);
      const before = session.authority.calls.length;
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await check(page.getByRole("complementary", { name: "Live preview panel" })).toHaveCount(0);
      expect(session.authority.calls).toHaveLength(before);
      session.authority.stopMode = "verified";
      await page.getByRole("button", { name: "See preview", exact: true }).click();
      await check(page.locator("iframe")).toHaveAttribute("title", "Live preview for agent-b");
      await noErrors(page);
    } finally {
      await session.close();
    }
  },
);
it.each(["reject", "unverified"] as const)(
  "%s stop retains the same frame and blocks replacement",
  async (mode) => {
    const session = await support.session();
    try {
      const page = await session.open();
      session.authority.stopMode = mode;
      await page.evaluate(() => {
        document.querySelector("iframe")?.setAttribute("data-original", "true");
      });
      await page.evaluate(() => window.previewFixture.see({ agent_id: "agent-b" }));
      await page.evaluate(() => window.previewFixture.see());
      await check(page.locator("iframe")).toHaveAttribute("data-original", "true");
      await check(page.getByRole("alert")).toContainText("Closure is unconfirmed");
      await check(page.getByRole("button", { name: "Close", exact: true })).toBeVisible();
      expect(session.authority.calls.filter((call) => call.kind === "start")).toHaveLength(1);
      await noErrors(page);
    } finally {
      await session.close();
    }
  },
);
it.each(["Close", "Leave preview"])(
  "%s sends stop and keeps the pending panel until verified termination",
  async (label) => {
    const session = await support.session();
    try {
      const page = await session.open();
      session.authority.gate = deferred<undefined>();
      await page.getByRole("button", { name: label, exact: true }).focus();
      await page.keyboard.press("Space");
      await check
        .poll(() => session.authority.calls.filter((call) => call.kind === "stop").length)
        .toBe(1);
      await check(page.getByRole("button", { name: "Close", exact: true })).toBeVisible();
      await check(page.locator("iframe")).toBeVisible();
      session.authority.gate.resolve(undefined);
      await check(page.getByRole("complementary", { name: "Live preview panel" })).toHaveCount(0);
      await noErrors(page);
    } finally {
      await session.close();
    }
  },
);
it("reload loses ownership; lost page-disappearance delivery relies on server expiry", async () => {
  const session = await support.session();
  try {
    const page = await session.open();
    session.authority.stopMode = "lost";
    await page.evaluate(() => {
      window.previewFixture.disappear();
    });
    await check.poll(() => session.authority.calls.some((call) => call.kind === "stop")).toBe(true);
    await page.reload();
    await page.waitForFunction(() => window.previewFixture.ready());
    const count = session.authority.calls.length;
    await page.evaluate(() => {
      window.previewFixture.advance(25_000);
    });
    expect(session.authority.calls).toHaveLength(count);
    await check(page.locator("iframe")).toHaveCount(0);
    expect(await page.evaluate(() => window.previewFixture.snapshot().owned)).toBe(false);
    session.clock.advance(30_001);
    await page.evaluate(() => window.previewFixture.observePublic());
    await check(page.getByRole("status")).toContainText(previewReasonCopy.lease_expired);
    expect(session.authority.calls.filter((call) => call.kind === "start")).toHaveLength(1);
    await noErrors(page);
  } finally {
    await session.close();
  }
});
it("bounds an in-session lost Close while the frame stays visible", async () => {
  const session = await support.session();
  try {
    const page = await session.open();
    session.authority.stopMode = "lost";
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await page.evaluate(() => {
      window.previewFixture.advance(5_001);
    });
    await check(page.getByRole("alert")).toContainText("Closure is unconfirmed");
    await check(page.locator("iframe")).toBeVisible();
    await noErrors(page);
  } finally {
    await session.close();
  }
});
it.each([1920, 390])(
  "preserves focused control and nonzero page/panel scroll at %i",
  async (width) => {
    const session = await support.session({ width, height: 844 });
    try {
      const page = await session.open();
      session.authority.stopMode = "reject";
      const before = await page.evaluate(() => {
        const slot = document.querySelector<HTMLElement>(".preview-fixture-slot");
        const panel = document.querySelector<HTMLElement>(".preview-panel-body");
        const control = document.querySelector<HTMLButtonElement>(".preview-panel button");
        if (!slot || !panel || !control) throw new Error("Missing scroll fixture");
        document.documentElement.style.overflow = "auto";
        document.body.style.overflow = "auto";
        document.body.style.minHeight = "2000px";
        slot.style.height = "350px";
        slot.style.minHeight = "350px";
        control.focus({ preventScroll: true });
        window.scrollTo(0, 60);
        panel.scrollTop = 80;
        return { page: scrollY, panel: panel.scrollTop };
      });
      expect(before.page).toBeGreaterThan(0);
      expect(before.panel).toBeGreaterThan(0);
      for (const operation of ["status", "replacement"]) {
        if (operation === "status")
          await page.evaluate(() => window.previewFixture.publish("ready", null));
        else await page.evaluate(() => window.previewFixture.see({ agent_id: "agent-b" }));
        await check(page.getByRole("button", { name: "Close", exact: true })).toBeFocused();
        expect(
          await page.evaluate(() => ({
            page: scrollY,
            panel: document.querySelector(".preview-panel-body")?.scrollTop,
          })),
        ).toEqual(before);
        expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe("IFRAME");
      }
      await noErrors(page);
    } finally {
      await session.close();
    }
  },
);
it("background ready details never focus the new iframe", async () => {
  const session = await support.session({ scenario: "starting" });
  try {
    const page = await session.open();
    const control = page.getByRole("button", { name: "Close", exact: true });
    await control.focus();
    await page.evaluate(() => window.previewFixture.publish("ready", null));
    await check(page.locator("iframe")).toBeVisible();
    await check(control).toBeFocused();
    await noErrors(page);
  } finally {
    await session.close();
  }
});
const surfaces: PreviewSurface[] = [
  "chat-wide",
  "chat-narrow",
  "oversight-watch",
  "oversight-focus",
  "usage",
  "settings",
  "inbox",
];
it.each(surfaces)("fake opening facts leave the built %s surface untouched", async (surface) => {
  const session = await support.session({ width: surface === "chat-narrow" ? 1280 : 1920 });
  try {
    const page = await session.open("surface", `surface=${surface}`);
    await page.waitForTimeout(100); // Let the existing lazy shell settle before measuring it.
    const facts = () =>
      page.evaluate(() => ({
        active: document.querySelector('[aria-selected="true"]')?.textContent,
        conversation: document.querySelector(".chat-review-conversation")?.getAttribute("hidden"),
        geometry: [
          ...document.querySelectorAll(".focus-panel, .oversight-stage, .chat-review-conversation"),
        ].map((node) => node.getBoundingClientRect().toJSON() as unknown),
        framing: [...document.querySelectorAll(".oversight-stage")].map((node) => node.innerHTML),
        scroll: [scrollX, scrollY],
        focus: document.activeElement?.outerHTML,
      }));
    const before = await facts();
    for (const preference of [false, true]) {
      expect(
        await page.evaluate(
          ({ surface, preference }) => window.previewFixture.finish(surface, { preference }),
          { surface, preference },
        ),
      ).toBe("see_preview");
      expect(await facts()).toEqual(before);
    }
    expect(session.authority.calls).toHaveLength(0);
    await check(page.getByRole("complementary", { name: "Live preview panel" })).toHaveCount(0);
    await check(page.getByLabel("Open preview when done")).toHaveCount(0);
    await noErrors(page);
  } finally {
    await session.close();
  }
});
it("wraps long multilingual candidate identities, preserves Close in forced colors and at 200%", async () => {
  const session = await support.session({ width: 390, height: 844 });
  try {
    const page = await session.open();
    const identity = "agent-" + "LongIdentity".repeat(12) + "العربية中文🙂";
    await page.evaluate(
      (agent_id) =>
        window.previewFixture.see({ agent_id, head_commit: "0123456789abcdef".repeat(8) }),
      identity,
    );
    await check(page.getByText(identity, { exact: true })).toBeVisible();
    await check(page.getByRole("button", { name: "Close", exact: true })).toBeVisible();
    await noErrors(page);
    await page.emulateMedia({ forcedColors: "active" });
    const close = page.getByRole("button", { name: "Close", exact: true });
    await close.focus();
    await check(close).toBeFocused();
    expect(await close.evaluate((button) => getComputedStyle(button).outlineWidth)).toBe("2px");
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.evaluate(() => {
      document.body.style.zoom = "2";
    });
    await check(close).toBeVisible();
    await noErrors(page);
  } finally {
    await session.close();
  }
});
it("measures only the allocated surface behind the existing narrow Usage notice", async () => {
  const session = await support.session({ width: 390, height: 844 });
  try {
    const page = await session.open("usage");
    await check(page.locator(".narrow-notice")).toBeVisible();
    const dimensions = await page.evaluate(() => {
      const focus = document.querySelector<HTMLElement>(".focus-panel");
      const usage = document.querySelector<HTMLElement>(".usage-scroll");
      return {
        allocated: focus?.clientWidth,
        child: usage?.clientWidth,
        childOverflow: usage ? usage.scrollWidth - usage.clientWidth : null,
      };
    });
    expect(dimensions.allocated).toBe(0);
    expect(dimensions.childOverflow).toBeGreaterThan(0);
  } finally {
    await session.close();
  }
});
