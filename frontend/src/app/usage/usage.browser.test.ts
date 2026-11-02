import { expect as check, type Route } from "@playwright/test";
import { expect, it } from "vitest";
import { recorded, early, empty } from "./usage.fixture";
import {
  required,
  enter,
  refresh,
  camera,
  row,
  groupingStyle,
  usageBrowserSupport,
} from "./usage.browser.support";

const { open } = usageBrowserSupport();

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
    await groupingStyle(page);
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
it("shows the local alert when the deferred Usage chunk fails to load", async () => {
  let chunkRequests = 0;
  let usageRequests = 0;
  const page = await open(recorded, {
    handler: (route) => {
      usageRequests++;
      return route.fulfill({ json: recorded });
    },
  });
  try {
    await page.route("**/src/app/usage/UsagePanel.tsx*", (route) => {
      chunkRequests++;
      return route.abort("failed");
    });
    await enter(page);
    await check(page.getByRole("alert")).toHaveText(
      "Usage could not load. Reload the page to try again.",
    );
    expect(chunkRequests).toBe(1);
    expect(usageRequests).toBe(0);
    await check(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
  } finally {
    await page.close();
  }
});
it.each([null, 0, 2])("renders unpriced costs and revision rounds %s", async (rounds) => {
  const page = await open({ ...recorded, revision_rounds: rounds });
  try {
    await enter(page);
    await check(row(page, "Agent B").getByRole("cell").nth(1)).toHaveText("No list price");
    await check(
      page
        .locator(".usage-fact")
        .filter({ has: page.getByText("Revision rounds", { exact: true }) })
        .locator("dd"),
    ).toHaveText(rounds === null ? "—" : String(rounds));
  } finally {
    await page.close();
  }
});
it.each(["dark", "day"])("switches fact columns at a 760px panel in %s", async (theme) => {
  const page = await open(recorded, { theme });
  try {
    await enter(page);
    await check(page.locator(".usage-scope")).toBeVisible();
    for (const width of [759, 760]) {
      // The focus panel has a 1px border outside the Usage content container.
      await page.locator(".focus-panel").evaluate((panel, width) => {
        panel.style.width = `${String(width + 1)}px`;
      }, width);
      const layout = await page.locator(".usage-facts").evaluate((facts) => {
        const [time, cost] = [...facts.children].map((node) => node.getBoundingClientRect());
        if (!time || !cost || !facts.parentElement?.parentElement)
          throw new Error("Missing fact groups");
        return {
          panel: facts.parentElement.parentElement.getBoundingClientRect().width,
          columns: getComputedStyle(facts).gridTemplateColumns.split(" ").length,
          gap: getComputedStyle(facts).gap,
          stacked: cost.top >= time.bottom,
          aligned: time.top === cost.top,
        };
      });
      expect(layout.panel).toBe(width);
      expect(layout.columns).toBe(width < 760 ? 1 : 2);
      expect(layout.gap).toBe(width < 760 ? "18px" : "44px");
      expect(layout.stacked).toBe(width < 760);
      expect(layout.aligned).toBe(width >= 760);
    }
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
