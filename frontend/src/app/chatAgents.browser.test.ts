import { execFile } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { expect as check, type Browser, type Locator, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./chatAgents.browser.fixture";
import { plan, proposal, resolved, seed, status, step, usage } from "./chatAgents.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/chat-agents-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/chatAgents.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function open(name = "working", width = 1920, theme = "dark", commands = true) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : 800 },
    reducedMotion: "reduce",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.chatAgentsTest);
  await page.evaluate(
    ({ name, theme, commands }) => {
      document.documentElement.dataset.theme = theme;
      window.chatAgentsTest.mount(name, commands);
    },
    { name, theme, commands },
  );
  await page.evaluate(() => document.fonts.ready);
  if (name !== "empty")
    await check(page.getByRole("list", { name: "Conversation timeline" })).toBeVisible();
  return page;
}
function assignedPlan(id: string, lead: string, agents: string[]) {
  const source = plan(id, lead);
  if (source.type !== "plan.updated") throw new Error("Missing plan");
  return {
    ...source,
    payload: {
      ...source.payload,
      state: "running" as const,
      subtasks: agents.map((agent_id) => ({
        agent_id,
        task: "Subtask",
        zone: { node_ids: [], paths: [] },
        depends_on: [],
        state: "running" as const,
      })),
    },
  };
}
const row = (page: Page, letter: string) =>
  page.locator(".chat-agent-row").filter({
    has: page.locator(".chat-agent-name", { hasText: new RegExp(`^Agent ${letter}$`) }),
  });
const view = (page: Page, name: string) =>
  page.getByRole("group", { name: "Workspace view" }).getByRole("button", { name, exact: true });
it("updates the running header as work, review, conflict and merge events arrive", async () => {
  const page = await open("empty");
  const count = page.locator(".chat-agents > header > span");
  try {
    await check(count).toHaveText("0 running");
    await page.evaluate(
      (events) => {
        window.chatAgentsTest.deliver(events);
      },
      [status(), status("agent-b", { slot: "B" })],
    );
    await check(count).toHaveText("2 running");
    await row(page, "A").click();
    await row(page, "B").focus();
    await page.evaluate(
      (events) => {
        window.chatAgentsTest.deliver(events);
      },
      [
        status("agent-a", { state: "ready_for_review" }),
        proposal("agent-a"),
        status("agent-b", { slot: "B", state: "paused" }),
      ],
    );
    await check(count).toHaveText("2 running");
    await page.evaluate(
      (events) => {
        window.chatAgentsTest.deliver(events);
      },
      [
        status("agent-b", { slot: "B", state: "halted" }),
        proposal("agent-b"),
        resolved("agent-b", true),
      ],
    );
    await check(count).toHaveText("2 running");
    await page.evaluate(
      (events) => {
        window.chatAgentsTest.deliver(events);
      },
      [resolved("agent-b")],
    );
    await check(count).toHaveText("1 running");
    await check(row(page, "A")).toHaveAttribute("aria-pressed", "true");
    await check(row(page, "B")).toBeFocused();
  } finally {
    await page.close();
  }
});
async function capturePlanLabels(page: Page, surface: string, width: number, theme: string) {
  if (process.env.CHAT_AGENTS_SHOTS !== "1") return;
  const directory = new URL("../../../.shots/chat-agents/", import.meta.url);
  await mkdir(fileURLToPath(directory), { recursive: true });
  await page.screenshot({
    path: fileURLToPath(new URL(`plan-labels-${surface}-${String(width)}-${theme}.png`, directory)),
    animations: "disabled",
  });
}
async function assertPlanSurfaces(page: Page, width: number, theme: string) {
  await capturePlanLabels(page, "chat", width, theme);
  await view(page, "Oversight").click();
  for (const ordinal of [1, 2]) {
    const label = `A·${String(ordinal)}`;
    await check(
      page.getByRole("tab", { name: new RegExp(`Agent ${label} · conversation$`) }),
    ).toBeVisible();
    const tile = page.getByRole("group", { name: `Agent ${label}: Working`, exact: true });
    await check(tile).toBeVisible();
    await check(tile).toContainText(label);
    expect(await tile.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  }
  await capturePlanLabels(page, "rail", width, theme);
  for (const ordinal of [1, 2])
    await page
      .getByRole("button", {
        name: `Close tab Agent A·${String(ordinal)} · conversation`,
        exact: true,
      })
      .click();
  for (const ordinal of [1, 2]) {
    const card = page.getByRole("article", {
      name: `Agent A·${String(ordinal)}`,
      exact: true,
    });
    await check(card).toBeVisible();
    await check(card.getByRole("heading")).toHaveText(`Agent A·${String(ordinal)}`);
    expect(await card.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  }
  await capturePlanLabels(page, "cards", width, theme);
  await view(page, "Chat").click();
}
it.each([
  [1920, "dark"],
  [1280, "dark"],
  [1280, "day"],
] as const)(
  "preserves plan identities on slot reuse and resets for a new run at %i in %s",
  async (width, theme) => {
    const page = await open("empty", width, theme);
    try {
      await page.evaluate(
        (events) => {
          window.chatAgentsTest.deliver(events);
        },
        [assignedPlan("p", "lead", ["first"]), status("first", { plan_id: "p", slot: "B" })],
      );
      await row(page, "A·1").click();
      await page.evaluate(
        (events) => {
          window.chatAgentsTest.deliver(events);
        },
        [
          resolved("first"),
          assignedPlan("q", "lead", ["second"]),
          status("second", { plan_id: "q", slot: "B" }),
        ],
      );
      await check(page.locator(".chat-agent-name")).toHaveText(["Agent A·1", "Agent A·2"]);
      await check(page.locator('.chat-agent-row[data-agent-origin="first"]')).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await page.locator('.chat-agent-row[data-agent-origin="second"]').click();
      await check(page.getByLabel("Agent A·2 conversation", { exact: true })).toBeVisible();
      await assertPlanSurfaces(page, width, theme);
      const start = seed.find((e) => e.type === "run.started");
      if (!start) throw new Error("Missing fixture run");
      await page.evaluate(
        (events) => {
          window.chatAgentsTest.deliver(events);
        },
        [{ ...start, payload: { ...start.payload, run_id: "c2-new-run" } }],
      );
      await check(page.locator(".chat-agent-row")).toHaveCount(0);
      await check(page.locator(".chat-agents > header > span")).toHaveText("0 running");
      await page.evaluate(
        (events) => {
          window.chatAgentsTest.deliver(events);
        },
        [status("fresh")],
      );
      await check(page.locator(".chat-agent-name")).toHaveText(["Agent A"]);
      await check(row(page, "A")).toHaveAttribute("aria-pressed", "false");
      await check(page.locator(".chat-agents > header > span")).toHaveText("1 running");
    } finally {
      await page.close();
    }
  },
);
async function identityContrast(page: Page, agent: Locator) {
  const samples = await agent.evaluate((el) =>
    [".chat-agent-badge", ".chat-agent-pill"].map((selector) => {
      const target = el.querySelector(selector);
      if (!target) throw new Error("Missing identity text");
      const rect = target.getBoundingClientRect();
      return {
        color: getComputedStyle(target).color,
        x: rect.x + (selector.endsWith("badge") ? 18 : 5),
        y: rect.y + (selector.endsWith("badge") ? 5 : rect.height / 2),
      };
    }),
  );
  const png = (await page.screenshot()).toString("base64");
  return page.evaluate(
    async ({ png, samples }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${png}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Missing contrast canvas");
      const luminance = (rgba: Uint8ClampedArray) =>
        [0.2126, 0.7152, 0.0722].reduce((sum, weight, index) => {
          const value = (rgba[index] ?? 0) / 255;
          return (
            sum + weight * (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
          );
        }, 0);
      return samples.map(({ x, y, color }) => {
        context.drawImage(image, 0, 0);
        const background = luminance(context.getImageData(Math.round(x), Math.round(y), 1, 1).data);
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        const foreground = luminance(context.getImageData(0, 0, 1, 1).data);
        return (
          (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)
        );
      });
    },
    { png, samples },
  );
}
it("keeps every team badge and active pill above AA contrast on selected rows in both themes", async () => {
  const page = await open("working", 1280);
  const readings: { theme: string; letter: string; ratios: number[] }[] = [];
  try {
    await page.evaluate(
      (events) => {
        window.chatAgentsTest.deliver(events);
      },
      ["C", "D", "E", "F"].map((letter) => status(`contrast-${letter}`)),
    );
    for (const theme of ["dark", "day"]) {
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      for (const letter of ["A", "B", "C", "D", "E", "F"]) {
        const agent = row(page, letter);
        await agent.click();
        await check(agent).toHaveAttribute("aria-pressed", "true");
        const ratios = await identityContrast(page, agent);
        readings.push({ theme, letter, ratios });
        for (const ratio of ratios) {
          expect(ratio, `${theme} Agent ${letter}`).toBeGreaterThanOrEqual(4.5);
        }
      }
    }

    if (process.env.CHAT_AGENTS_SHOTS === "1") {
      const directory = new URL("../../../.shots/chat-agents/", import.meta.url);
      await mkdir(fileURLToPath(directory), { recursive: true });
      await writeFile(
        new URL("identity-contrast.json", directory),
        JSON.stringify(readings, null, 2),
      );
    }
  } finally {
    await page.close();
  }
});
it("activates A/B conversations and command targets, with focus separate from selection", async () => {
  const page = await open();
  try {
    const a = row(page, "A"),
      b = row(page, "B");
    await b.click();
    await check(b).toBeFocused();
    await check(b).toHaveAttribute("aria-pressed", "true");
    await check(page.getByLabel("Agent B conversation", { exact: true })).toBeVisible();
    const composer = page.getByRole("textbox", { name: "Message Agent B" });
    await composer.fill("Target B");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    expect(await page.evaluate(() => window.chatAgentsTest.calls())).toEqual([
      { id: "agent-b", text: "Target B" },
    ]);
    await a.focus();
    await a.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await check(a).toBeFocused();
    for (const target of [a, b]) {
      await target.focus();
      for (const modifier of ["ctrlKey", "altKey", "metaKey", "shiftKey"])
        for (const key of ["Home", "End", "ArrowUp", "ArrowDown"]) {
          const prevented = await target.evaluate(
            (el, init) => {
              const options = { bubbles: true, cancelable: true };
              const event = new KeyboardEvent("keydown", { ...options, ...init });
              el.dispatchEvent(event);
              return event.defaultPrevented;
            },
            { key, [modifier]: true },
          );
          expect(prevented).toBe(false);
          await check(target).toBeFocused();
        }
    }
    await a.focus();
    await a.press("End");
    await check(b).toBeFocused();
    await b.press("Home");
    await check(a).toBeFocused();
    await check(b).toHaveAttribute("aria-pressed", "true");
    expect(await a.evaluate((el) => getComputedStyle(el).outlineWidth)).toBe("2px");
    await a.press("ArrowDown");
    await check(b).toBeFocused();
    await b.press("ArrowUp");
    await a.press("Enter");
    await check(a).toBeFocused();
    await check(a).toHaveAttribute("aria-pressed", "true");
    await a.press("ArrowDown");
    await b.press("Space");
    await check(b).toHaveAttribute("aria-pressed", "true");
    await b.press("Tab");
    expect(await b.evaluate((el) => el === document.activeElement)).toBe(false);
  } finally {
    await page.close();
  }
});
it("keeps selection, draft and focus through events, layouts and replay navigation", async () => {
  const page = await open();
  try {
    const a = row(page, "A"),
      b = row(page, "B");
    await b.focus();
    await page.evaluate(
      (events) => {
        window.chatAgentsTest.deliver(events);
      },
      [
        status("agent-a", { state: "paused" }),
        step("agent-a"),
        usage("agent-a", null),
        proposal("agent-a"),
        resolved("agent-a"),
      ],
    );
    await check(b).toBeFocused();
    await check(a).toHaveAttribute("aria-pressed", "true");
    const composer = page.getByRole("textbox", { name: "Message Agent A" });
    await composer.fill("Keep draft");
    await composer.focus();
    await page.evaluate(
      (events) => {
        window.chatAgentsTest.deliver(events);
      },
      [
        status("agent-b", { slot: "B", state: "halted" }),
        step("agent-b"),
        usage("agent-b", 0),
        proposal("agent-b"),
        resolved("agent-b", true),
      ],
    );
    await check(composer).toBeFocused();
    await check(a).toHaveAttribute("aria-pressed", "true");
    await view(page, "Oversight").click();
    await check(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
    await check(composer).toHaveValue("Keep draft");
    await page.waitForFunction(
      () =>
        !!(
          document.querySelector('[aria-label="Repository graph"]') as HTMLElement & {
            oversightGraph?: unknown;
          }
        ).oversightGraph,
    );
    const graph = await page.getByLabel("Repository graph", { exact: true }).elementHandle();
    await view(page, "Chat").click();
    await check(a).toHaveAttribute("aria-pressed", "true");
    await check(composer).toHaveValue("Keep draft");
    await view(page, "Oversight").click();
    expect(await graph.evaluate((el) => el.isConnected)).toBe(true);
    await page.evaluate(() => {
      window.chatAgentsTest.mount("working", false);
    });
    const mutations: string[] = [];
    page.on("request", (r) => {
      if (r.method() !== "GET") mutations.push(r.url());
    });
    await row(page, "B").click();
    await check(page.getByLabel("Agent B conversation", { exact: true })).toBeVisible();
    expect(mutations).toEqual([]);
    expect(await page.evaluate(() => window.chatAgentsTest.calls())).toEqual([]);
  } finally {
    await page.close();
  }
});
it("scrolls the list independently and wraps team identities at 1280 without clipping state", async () => {
  const page = await open("working", 1280);
  try {
    await page.evaluate(() => {
      window.chatAgentsTest.longTeam();
    });
    const body = page.locator(".chat-agents .chat-side-body"),
      timeline = page.getByRole("list", { name: "Conversation timeline" });
    const before = await timeline.evaluate((el) => el.scrollTop);
    await body.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    expect(await body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
    expect(await timeline.evaluate((el) => el.scrollTop)).toBe(before);
    const last = row(page, "C·12");
    await check(last).toBeVisible();
    await check(last.locator(".chat-agent-pill")).toHaveText("Ready for review");
    const fit = await last.evaluate((el) => {
      const badge = el.querySelector(".chat-agent-badge"),
        pill = el.querySelector(".chat-agent-pill"),
        name = el.querySelector(".chat-agent-name");
      if (!badge || !pill || !name || !badge.firstChild) throw new Error("Missing row anatomy");
      return {
        overflow: el.scrollWidth > el.clientWidth,
        badge: (() => {
          const range = document.createRange();
          range.selectNodeContents(badge.firstChild);
          return range.getBoundingClientRect().width <= badge.clientWidth;
        })(),
        wrap: pill.getBoundingClientRect().top > name.getBoundingClientRect().top,
      };
    });
    expect(fit).toEqual({ overflow: false, badge: true, wrap: true });
    await check(row(page, "A")).toHaveAttribute("aria-pressed", "true");
  } finally {
    await page.close();
  }
});
it("keeps long model text and multilingual tasks readable with accessible activity and cost", async () => {
  const page = await open("working", 1280);
  try {
    const model = "LongModel".repeat(20),
      task = "مرحبا 世界 🧪 ".repeat(20),
      detail = "Wait for the provider to recover. ".repeat(10);
    await page.evaluate(
      (events) => {
        window.chatAgentsTest.deliver(events);
      },
      [status("agent-a", { model, task, detail, state: "halted" }), usage("agent-a", null)],
    );
    const a = row(page, "A");
    await check(a).toHaveAccessibleName(`Agent A: ${task}. Halted. agent-a`);
    await check(a).toHaveAccessibleDescription(`${model}; ${detail}; Step 4 of 30; Cost unknown`);
    const modelText = a.locator(".chat-agent-model");
    expect(await modelText.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await a.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.emulateMedia({ forcedColors: "active" });
    await a.press("Home");
    await check(a).toBeFocused();
    expect(await a.evaluate((el) => getComputedStyle(el).outlineWidth)).toBe("2px");
    await check(a).toHaveAttribute("aria-pressed", "true");
  } finally {
    await page.close();
  }
});
for (const name of ["empty", "working", "proposed", "review", "merged"])
  for (const width of [1920, 1280])
    for (const theme of ["dark", "day"]) {
      it.skipIf(process.env.CHAT_AGENTS_SHOTS !== "1")(
        `captures ${name}-${String(width)}-${theme}`,
        async () => {
          const page = await open(name, width, theme);
          try {
            await check(page.locator(".chat-agents > header")).toContainText(
              `${name === "empty" ? "0" : name === "merged" ? "1" : "2"} running`,
            );
            if (name !== "empty")
              await check(row(page, name === "merged" ? "B" : "A")).toHaveAttribute(
                "aria-pressed",
                "true",
              );
            const geometry = await page.locator(".chat-agents").boundingBox();
            await page.evaluate(
              () =>
                new Promise<void>((resolve) =>
                  requestAnimationFrame(() =>
                    requestAnimationFrame(() => {
                      resolve();
                    }),
                  ),
                ),
            );
            expect(await page.locator(".chat-agents").boundingBox()).toEqual(geometry);
            const path = fileURLToPath(
              new URL(
                `../../../.shots/chat-agents/${name}-${String(width)}-${theme}`,
                import.meta.url,
              ),
            );
            await mkdir(fileURLToPath(new URL("../../../.shots/chat-agents", import.meta.url)), {
              recursive: true,
            });
            await page.screenshot({ path: `${path}.png`, animations: "disabled" });
            const measured = await page.evaluate(() =>
              Object.fromEntries(
                [
                  ".chat-agents",
                  ".chat-agents > header",
                  ".chat-agent-list",
                  ".chat-agent-row",
                  ".chat-agent-badge",
                  ".chat-agent-lamp",
                  ".chat-agent-name",
                  ".chat-agent-task",
                  ".chat-agent-pill",
                  ".chat-agent-facts",
                ].map((selector) => {
                  const el = document.querySelector(selector);
                  if (!el) return [selector, null];
                  const s = getComputedStyle(el),
                    r = el.getBoundingClientRect();
                  return [
                    selector,
                    {
                      width: r.width,
                      height: r.height,
                      padding: s.padding,
                      gap: s.gap,
                      radius: s.borderRadius,
                      border: s.borderWidth,
                      size: s.fontSize,
                      weight: s.fontWeight,
                      line: s.lineHeight,
                    },
                  ];
                }),
              ),
            );
            await writeFile(`${path}.json`, JSON.stringify(measured, null, 2));
          } finally {
            await page.close();
          }
        },
      );
    }

// Local evidence is deliberately uncommitted; run after captures and the comparison script.
const evidence = process.env.CHAT_AGENTS_EVIDENCE === "1";
const evidenceRoot = new URL("../../../.shots/chat-agents/", import.meta.url);
const decisionsUrl = new URL("../../../DECISIONS.md", import.meta.url);
it.runIf(evidence)(
  "verifies all 20 full-size capture-left/reference-right comparisons",
  async () => {
    const page = await browser.newPage();
    try {
      for (const state of ["empty", "working", "proposed", "review", "merged"])
        for (const width of [1920, 1280])
          for (const theme of ["dark", "day"]) {
            const name = `${state}-${String(width)}-${theme}`;
            const reference = `${state}-${width === 1920 ? "" : "1280-"}${theme}.png`;
            const sources = await Promise.all(
              [
                new URL(`${name}.png`, evidenceRoot),
                new URL(`../chat-r1-reference/${reference}`, evidenceRoot),
                new URL(`compare/${name}.png`, evidenceRoot),
              ].map(
                async (path) =>
                  `data:image/png;base64,${(await readFile(path)).toString("base64")}`,
              ),
            );
            const result = await page.evaluate(async (sources) => {
              const images = await Promise.all(
                sources.map(async (src) => {
                  const image = new Image();
                  image.src = src;
                  await image.decode();
                  return image;
                }),
              );
              const [capture, reference, comparison] = images;
              if (!capture || !reference || !comparison) throw new Error("Missing image");
              const canvas = document.createElement("canvas");
              canvas.width = comparison.width;
              canvas.height = comparison.height;
              const context = canvas.getContext("2d", { willReadFrequently: true });
              if (!context) throw new Error("Missing evidence canvas");
              context.drawImage(capture, 0, 0);
              context.drawImage(reference, capture.width, 0);
              const expected = context.getImageData(0, 0, canvas.width, canvas.height).data;
              context.clearRect(0, 0, canvas.width, canvas.height);
              context.drawImage(comparison, 0, 0);
              const actual = context.getImageData(0, 0, canvas.width, canvas.height).data;
              return {
                dimensions: images.map((image) => [image.width, image.height]),
                same: expected.every((value, index) => value === actual[index]),
              };
            }, sources);
            const height = width === 1920 ? 1080 : 800;
            expect(result, name).toEqual({
              dimensions: [
                [width, height],
                [width, height],
                [width * 2, height],
              ],
              same: true,
            });
            const measured = JSON.parse(
              await readFile(new URL(`${name}.json`, evidenceRoot), "utf8"),
            ) as Record<string, { width: number; height: number }>;
            expect(measured[".chat-agents"]?.width).toBe(width === 1920 ? 280 : 260);
            expect(measured[".chat-agents > header"]?.height).toBe(56);
          }
    } finally {
      await page.close();
    }
  },
  60_000,
);

it.runIf(evidence)("verifies the 160 measured pairs against fresh computed styles", async () => {
  const measurements = JSON.parse(
    await readFile(new URL("measurements.json", evidenceRoot), "utf8"),
  ) as Record<string, Record<string, Record<string, string | number>>>;
  const table = JSON.parse(
    await readFile(new URL("measurements-delta.json", evidenceRoot), "utf8"),
  ) as {
    measurements: {
      viewport: string;
      pairs: {
        selector: string;
        key: string;
        approved: string | number;
        build: string | number;
        delta: number;
      }[];
    }[];
  };
  expect(table.measurements.map(({ viewport }) => viewport).sort()).toEqual([
    "1280-dark",
    "1280-day",
    "1920-dark",
    "1920-day",
  ]);
  for (const { viewport, pairs } of table.measurements) {
    expect(pairs).toHaveLength(40);
    for (const pair of pairs) {
      expect(pair.build).toBe(measurements[viewport]?.[pair.selector]?.[pair.key]);
      const numeric = (value: string | number) => Number.parseFloat(String(value));
      const expected =
        pair.approved === pair.build || String(pair.build).includes(String(pair.approved))
          ? 0
          : numeric(pair.build) - numeric(pair.approved);
      expect(pair.delta).toBe(expected);
    }
  }
});

it.runIf(evidence)("verifies documented per-chunk startup deltas and both caps", async () => {
  const startup = JSON.parse(
    await readFile(new URL("startup-delta.json", evidenceRoot), "utf8"),
  ) as {
    before: { static: number; effective: number };
    after: { static: number; effective: number };
    chunks: {
      beforeFile: string;
      afterFile: string;
      beforeGzip: number;
      afterGzip: number;
      delta: number;
      scope: string;
    }[];
  };
  const decisions = (await readFile(decisionsUrl, "utf8")).split("## 2026-11-01 — C2 ")[1] ?? "";
  expect(startup.chunks).toHaveLength(26);
  for (const chunk of startup.chunks) {
    expect(chunk.delta).toBe(chunk.afterGzip - chunk.beforeGzip);
    const row = `| ${chunk.beforeFile} | ${chunk.afterFile} | ${String(chunk.beforeGzip)} | ${String(chunk.afterGzip)} | ${String(chunk.delta)} |`;
    expect(decisions.includes(row), `Missing chunk readings: ${chunk.afterFile}`).toBe(true);
  }
  for (const scope of ["static", "effective"] as const) {
    const chunks = startup.chunks.filter(
      (chunk) => scope === "effective" || chunk.scope === "static",
    );
    expect(chunks.reduce((sum, chunk) => sum + chunk.beforeGzip, 0)).toBe(startup.before[scope]);
    expect(chunks.reduce((sum, chunk) => sum + chunk.afterGzip, 0)).toBe(startup.after[scope]);
    expect(startup.before[scope]).toBeLessThanOrEqual(409_600);
    expect(startup.after[scope]).toBeLessThanOrEqual(409_600);
  }
});

it.runIf(evidence)("keeps the documented C2 slice totals current after the merge", async () => {
  // Git runs in an asyncio subprocess with a timeout, including in this evidence check.
  const { stdout } = await promisify(execFile)(
    "python3",
    [
      "-c",
      `
import asyncio
async def main():
    process = await asyncio.create_subprocess_exec(
        'git', 'diff', '--numstat', '0fa0f274',
        stdout=asyncio.subprocess.PIPE)
    output, _ = await asyncio.wait_for(process.communicate(), 30)
    if process.returncode: raise RuntimeError('git diff failed')
    counts = [line.split('\\t')[:2] for line in output.decode().splitlines()]
    print(sum(int(a) for a, _ in counts), sum(int(d) for _, d in counts))
asyncio.run(main())
  `,
    ],
    { cwd: fileURLToPath(new URL("../../../", import.meta.url)), timeout: 35_000 },
  );
  const [added = 0, deleted = 0] = stdout.trim().split(" ").map(Number);
  const decisions = await readFile(decisionsUrl, "utf8");
  const totals = `${String(added)} additions, ${String(deleted)} deletions, ${String(added + deleted)} changed lines`;
  expect(decisions.includes(totals), `Missing current slice: ${totals}`).toBe(true);
});
