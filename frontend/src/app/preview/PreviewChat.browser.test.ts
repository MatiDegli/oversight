import type { ServerEvent } from "@contracts";
import type { GraphProbeHost } from "../../graph";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";
import { applyEvent, createInitialState } from "../../state";
import {
  readSsePrefix,
  coldStartApi,
  coldStartSources,
  coldStartStress,
  previewChatSupport,
  chatSizes,
  chatDirectory,
} from "./previewChat.browser.support";
import { captureCheckpoint } from "./previewPanel.browser.support";
const support = previewChatSupport();
const matrix = chatSizes.flatMap((viewport) =>
  ["dark", "day"].map((theme) => ({ viewport, theme })),
);
it.each([
  ["transport/index.ts", 'import.meta.env.MODE === "replay"'],
  ["transport/index.ts", "export const replaySource = chooseReplaySource("],
  ["transport/index.ts", "onReplay?.(player);"],
  ["state/store.ts", "export const stateStore = createStateStore();"],
  ["transport/live.ts", "      onError?.(error);"],
  ["transport/live.ts", "          retryWindow = 500;"],
  ["app/preview/PreviewPanel.tsx", "progress.load(frame);"],
])("rejects missing or ambiguous proof anchors in %s: %s", async (path, anchor) => {
  const plugin = support.server?.config.plugins.find(({ name }) => name === "preview-chat-proof");
  const hook = plugin?.transform;
  if (!hook) throw new Error("Missing proof transform");
  const transform = typeof hook === "function" ? hook : hook.handler;
  const source = await readFile(new URL(`../../${path}`, import.meta.url), "utf8");
  const duplicate = anchor.startsWith("export const replaySource")
    ? source.match(/export const replaySource = chooseReplaySource\([\s\S]*?\n\);/)?.[0]
    : anchor;
  if (!duplicate) throw new Error("Missing replay selection fixture");
  for (const drifted of [source.replace(anchor, "/* anchor drift */"), `${source}\n${duplicate}`])
    expect(() => {
      Reflect.apply(transform, {}, [drifted, `/src/${path}`]);
    }).toThrow(`Expected one proof instrumentation anchor in /src/${path}`);
});
it.each(["dark", "day"])("keeps Preview app visually primary in %s", async (theme) => {
  const session = await support.session(chatSizes[0], theme);
  try {
    const appearance = await session.page.locator(".lead-preview-actions").evaluate((node) => {
      const primary = node.querySelector(".lead-preview-primary");
      const secondary = node.querySelector(".lead-preview-secondary");
      if (!primary || !secondary) throw new Error("Missing preview actions");
      const token = (name: string) => {
        const probe = document.createElement("span");
        probe.style.color = `var(--${name})`;
        node.append(probe);
        const color = getComputedStyle(probe).color;
        probe.remove();
        return color;
      };
      return {
        primaryFill: getComputedStyle(primary).backgroundColor,
        primaryText: getComputedStyle(primary).color,
        secondaryFill: getComputedStyle(secondary).backgroundColor,
        secondaryText: getComputedStyle(secondary).color,
        secondaryBorder: getComputedStyle(secondary).borderStyle,
        text: token("text"),
        page: token("page"),
      };
    });
    expect(appearance.primaryFill).toBe(appearance.text);
    expect(appearance.primaryText).toBe(appearance.page);
    expect(appearance.secondaryFill).toBe("rgba(0, 0, 0, 0)");
    expect(appearance.secondaryText).toBe(appearance.text);
    expect(appearance.secondaryBorder).toBe("solid");
  } finally {
    await session.close();
  }
});
it("records the capture HEAD and working-tree state beside the Chat PNG", async () => {
  const session = await support.session();
  try {
    const checkpoint = await captureCheckpoint();
    const manifest = await support.capture(session, "option");
    expect(manifest).toMatchObject(checkpoint);
    expect(manifest.checkpoint).toMatch(/^[a-f0-9]{40}$/);
    const saved: unknown = JSON.parse(
      await readFile(`${chatDirectory}fixture/option-1920-dark.json`, "utf8"),
    );
    expect(saved).toEqual(manifest);
    expect(typeof (saved as Record<string, unknown>).workingTree).toBe("boolean");
  } finally {
    await session.close();
  }
});
it.each([-86_400_000, 86_400_000])(
  "measures preview durations across a wall-clock adjustment of %i ms",
  async (offset) => {
    const session = await support.session(chatSizes[2]);
    try {
      await session.page.getByRole("button", { name: "Preview app", exact: true }).click();
      await check(session.page.locator(".preview-panel-phase")).toContainText("App starting");
      await session.page.evaluate((delta) => {
        const original = Date.now;
        Date.now = () => original() + delta;
      }, offset);
      session.publish("ready");
      await check(
        session.page.frameLocator("iframe").getByRole("heading", { name: "Candidate shop" }),
      ).toBeVisible();
      const manifest = await support.capture(session, "clock-adjustment");
      expect(manifest.timeToReadyMs).toBeGreaterThan(0);
      expect(manifest.timeToReadyMs).toBeLessThan(10_000);
      expect(manifest.timeToInteractiveMs).toBeGreaterThan(0);
      expect(manifest.timeToInteractiveMs).toBeLessThan(10_000);
    } finally {
      await session.close();
    }
  },
);
it.each(matrix)(
  "proves App option/starting/shop/refusal/replay at $viewport.width in $theme",
  async ({ viewport, theme }) => {
    const session = await support.session(viewport, theme);
    const { page } = session;
    try {
      const option = page.getByRole("button", { name: "Preview app", exact: true });
      const review = page.getByRole("button", { name: "Review change", exact: true });
      await check(option).toBeEnabled();
      await check(review).toBeEnabled();
      await check(page.locator("iframe")).toHaveCount(0);
      const reply = page
        .locator("[role=listitem]")
        .filter({ hasText: "The coupon change is ready to try." });
      expect(await reply.count()).toBe(1);
      expect(
        await page
          .locator(".lead-preview-option")
          .evaluate((node) => node.closest("[role=listitem]")?.previousElementSibling?.textContent),
      ).toContain("The coupon change is ready to try.");
      await support.capture(session, "option");
      const composer = page.locator(".conversation-composer textarea");
      if (await composer.count()) await composer.fill("Keep this draft");
      await option.focus();
      const rail = await page.locator(".chat-agents").boundingBox();
      const original = await page.locator(".conversation-panel").boundingBox();
      const beforeOpenRenders = await page.evaluate(() => ({ ...window.previewChatRenders }));
      await option.press("Enter");
      await check(page.locator(".preview-panel-phase")).toContainText("App starting");
      await support.capture(session, "starting");
      const openedRenders = await page.evaluate(() => window.previewChatRenders);
      for (const key of ["shell", "conversation", "graph"])
        expect(openedRenders[key]).toBe(beforeOpenRenders[key]);
      const optionBox = await page.locator(".lead-preview-option").boundingBox();
      const timelineBox = await page.locator(".conversation-timeline").boundingBox();
      if (viewport.width >= 768 && optionBox && timelineBox)
        expect(optionBox.y + optionBox.height).toBeLessThanOrEqual(
          timelineBox.y + timelineBox.height,
        );
      const before = await page.locator(".conversation-composer").boundingBox();
      const replyBefore = await reply.boundingBox();
      const counters = await page.evaluate(() => ({ ...window.previewChatRenders }));
      const focus = await page.evaluate(() => document.activeElement?.outerHTML);
      await page.waitForTimeout(1100); // One real elapsed tick, bounded to this header.
      session.publish("ready");
      await check(
        page.frameLocator("iframe").getByRole("heading", { name: "Candidate shop" }),
      ).toBeVisible();
      await check(page.locator("iframe")).toHaveAttribute(
        "sandbox",
        "allow-scripts allow-same-origin allow-forms",
      );
      await check(page.locator("iframe")).toHaveAttribute("referrerpolicy", "no-referrer");
      expect(await page.locator(".conversation-composer").boundingBox()).toEqual(before);
      expect(await reply.boundingBox()).toEqual(replyBefore);
      expect(await page.evaluate(() => document.activeElement?.outerHTML)).toEqual(focus);
      const after = await page.evaluate(() => window.previewChatRenders);
      expect(after.shell).toBe(counters.shell);
      expect(after.conversation).toBe(counters.conversation);
      expect(after.graph).toBe(counters.graph);
      expect(after.phase).toBeGreaterThan(counters.phase ?? 0);
      const manifest = await support.capture(session, "ready");
      expect(manifest.timeToReadyMs).toBeGreaterThan(0);
      await check(page.locator(".preview-panel button")).toBeVisible();
      await page.locator(".preview-panel button").click();
      await check(page.locator(".chat-preview-slot")).toHaveCount(0);
      const closedRenders = await page.evaluate(() => window.previewChatRenders);
      for (const key of ["shell", "conversation", "graph"])
        expect(closedRenders[key]).toBe(after[key]);
      expect(await page.locator(".chat-agents").boundingBox()).toEqual(rail);
      expect(await page.locator(".conversation-panel").boundingBox()).toEqual(original);
      if (await composer.count()) await check(composer).toHaveValue("Keep this draft");
      await check(page.locator(".chat-work")).toHaveCount(1);
      await option.click();
      session.publish("failed", "isolation_unavailable");
      await check(page.getByText("Preview refused", { exact: true })).toBeVisible();
      await support.capture(session, "refused");
      await page.locator(".preview-panel button").click();
    } finally {
      await session.close();
    }
    const replay = await support.session(viewport, theme, "replay=true");
    try {
      await check(
        replay.page.getByRole("button", { name: "Preview app", exact: true }),
      ).toBeDisabled();
      await check(
        replay.page.getByRole("button", { name: "Review change", exact: true }),
      ).toBeDisabled();
      await check(replay.page.locator("iframe")).toHaveCount(0);
      await support.capture(replay, "replay");
      expect(replay.calls.start).toBe(0);
    } finally {
      await replay.close();
    }
  },
  40_000,
);
it.each(["expired", "observer", "uncertain", "replay"])(
  "captures the explicitly inert %s panel fixture",
  async (state) => {
    const session = await support.session(chatSizes[2], "dark", `panelFixture=${state}`);
    try {
      await support.capture(session, `panel-fixture-${state}`);
      await check(session.page.locator("iframe")).toHaveCount(0);
    } finally {
      await session.close();
    }
  },
);
it("Review change targets the resolved proposal without sending any decision", async () => {
  const session = await support.session();
  try {
    await session.page.getByRole("button", { name: "Review change", exact: true }).press("Space");
    await check(session.page.getByRole("complementary", { name: "Review panel" })).toBeVisible();
    expect(session.calls.start).toBe(0);
    expect(session.requests).toEqual([]);
  } finally {
    await session.close();
  }
});

it("a later matching reply moves the same option DOM without moving its keyboard focus", async () => {
  const session = await support.session(chatSizes[1], "dark", "replyLate=true");
  try {
    const button = session.page.getByRole("button", { name: "Preview app", exact: true });
    await check(
      session.page.getByText("Preview suggested; no matching lead reply is available."),
    ).toBeVisible();
    await button.focus();
    session.reply();
    await check(
      session.page.getByText("Preview suggested; no matching lead reply is available."),
    ).toHaveCount(0);
    await check(button).toBeFocused();
    expect(session.calls.start).toBe(0);
  } finally {
    await session.close();
  }
});
it("edits invalidate the suggestion and arrivals cannot open the slot or steal focus", async () => {
  const session = await support.session();
  try {
    const composer = session.page.locator(".conversation-composer textarea");
    await composer.focus();
    session.invalidate();
    await check(session.page.getByRole("button", { name: "Preview app", exact: true })).toHaveCount(
      0,
    );
    await check(composer).toBeFocused();
    await check(session.page.locator(".chat-preview-slot")).toHaveCount(0);
    expect(session.calls.start).toBe(0);
  } finally {
    await session.close();
  }
});
it("resize and explicit navigation retain one cold-to-warm graph and cancel the live frame", async () => {
  const session = await support.session();
  const { page } = session;
  try {
    await check(page.getByRole("img", { name: "Repository graph" })).toHaveCount(0);
    await page.getByRole("button", { name: "Preview app", exact: true }).click();
    session.publish("ready");
    await check(page.locator("iframe")).toBeVisible();
    const close = page.locator(".preview-panel button");
    await close.focus();
    await page.setViewportSize({ width: 1280, height: 800 });
    await check(close).toBeFocused();
    await page.getByRole("button", { name: "Oversight", exact: true }).click();
    await check(page.locator("iframe")).toHaveCount(0);
    await check(page.getByRole("img", { name: "Repository graph" })).toHaveCount(1);
    await page.waitForFunction(() =>
      Boolean(
        document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
      ),
    );
    const graph = await page.getByRole("img", { name: "Repository graph" }).elementHandle();
    await page.getByRole("button", { name: "Chat", exact: true }).click();
    await check(page.locator(".chat-preview-slot")).toHaveCount(0);
    await page.getByRole("button", { name: "Oversight", exact: true }).click();
    await check(page.getByRole("img", { name: "Repository graph" })).toHaveCount(1);
    expect(
      await graph.evaluate(
        (node) => node === document.querySelector('[aria-label="Repository graph"]'),
      ),
    ).toBe(true);
    expect(session.calls.stop).toBe(1);
    await page.getByRole("button", { name: "Chat", exact: true }).click();
    const warmed = await page.evaluate(() => ({ ...window.previewChatRenders }));
    await page.getByRole("button", { name: "Preview app", exact: true }).click();
    session.publish("ready");
    await check(page.locator("iframe")).toBeVisible();
    const previewed = await page.evaluate(() => window.previewChatRenders);
    for (const key of ["shell", "conversation", "graph"]) expect(previewed[key]).toBe(warmed[key]);
    await page.locator(".preview-panel button").click();
    await check(page.locator("iframe")).toHaveCount(0);
    const exposed = await page.evaluate(() => ({
      html: document.body.innerHTML,
      url: location.href,
      storage: [Object.keys(localStorage), Object.keys(sessionStorage)],
    }));
    expect(JSON.stringify(exposed)).not.toMatch(/lease_token|F{43}/);
  } finally {
    await session.close();
  }
});

// Baseline 103938e4: a single stored backlog reproduces at seq 53 in recorded-demo
// (69 events / 125491 source bytes) and scripted-level2 (98 / 167027), even though
// after-based retries finish the reducer prefix. pageerror alone misses both failures.
// All 23 complete fixtures pass the public player's production 1×/seek paths.
// Measure cold-start backlogs through real EventSource -> App, with source envelopes untouched.
await mkdir(`${chatDirectory}../cold-start-depth`, { recursive: true });
const measuredSources = (await coldStartSources()).filter((source) => source.complete);
it.each(measuredSources)(
  "applies the complete SSE cold start of $path without application/React errors",
  async (source) => {
    const session = await support.session(chatSizes[0], "dark", "measure=true", source.events);
    try {
      await check(session.page.locator("#root")).toHaveAttribute(
        "data-seq",
        String(source.events.at(-1)?.seq),
      );
      const trace = await session.page.evaluate(() => window.coldStartTrace);
      const failures = await session.page.evaluate(() => window.coldStartFailures ?? []);
      await writeFile(
        `${chatDirectory}../cold-start-depth/${String(source.path.split("/").at(-1))}-live.json`,
        JSON.stringify(
          {
            source: source.path,
            bytes: source.bytes,
            events: source.events.length,
            beforeLeadView: source.beforeLeadView,
            deliveries: support.deliveries,
            cursors: trace.cursors,
            errors: session.pageErrors,
            failures,
            console: session.diagnostics,
          },
          null,
          2,
        ) + "\n",
      );
      expect(trace.cursors).toEqual(source.events.map((event) => event.seq));
      expect(trace.state).toEqual(source.events.reduce(applyEvent, createInitialState()));
      expect(await session.page.evaluate(() => window.coldStartFailures ?? [])).toEqual([]);
      expect(await session.page.evaluate(() => window.coldStartAlerts ?? [])).toEqual([]);
      expect(session.pageErrors).toEqual([]);
      expect(session.diagnostics.filter((item) => item.type === "error")).toEqual([]);
      await check(session.page.getByRole("contentinfo", { name: "Status line" })).not.toContainText(
        "Connection lost",
      );
      expect(support.deliveries.map((delivery) => delivery.after)).toEqual([0]);
      await session.page.waitForTimeout(100);
      const settled = await session.page.evaluate(() => ({ ...window.previewChatRenders }));
      await session.page.waitForTimeout(250);
      expect(await session.page.evaluate(() => window.previewChatRenders)).toEqual(settled);
      await check(
        session.page
          .getByRole("alert")
          .filter({ hasText: /Maximum update depth|connection lost|Transport failed/i }),
      ).toHaveCount(0);
    } finally {
      await session.close();
    }
  },
  30_000,
);

it.each(measuredSources)(
  "plays $path through App's public player at recorded 1× timing and seek boundaries",
  async (source) => {
    const bundled = source.path.endsWith("recorded-demo.jsonl");
    const session = await support.session(
      chatSizes[0],
      "dark",
      `replay=true&${bundled ? "publicRecording=true" : ""}`,
      source.events,
    );
    const { page } = session;
    try {
      await page.waitForFunction(() => Boolean(window.coldStartPlayer));
      const duration = await page.evaluate(
        () => window.coldStartPlayer?.getSnapshot().duration ?? 0,
      );
      await page.clock.runFor(duration + 40);
      await check(page.locator("#root")).toHaveAttribute(
        "data-seq",
        String(source.events.at(-1)?.seq),
      );
      const trace = await page.evaluate(() => window.coldStartTrace);
      expect(trace.state).toEqual(source.events.reduce(applyEvent, createInitialState()));
      for (const seq of [1, Math.min(53, source.events.length), source.events.length]) {
        await page.evaluate((seq) => window.coldStartPlayer?.seekSeq(seq), seq);
        await page.clock.runFor(50);
        expect(await page.evaluate(() => window.coldStartTrace.state)).toEqual(
          source.events.slice(0, seq).reduce(applyEvent, createInitialState()),
        );
      }
      await page.evaluate(() => {
        const player = window.coldStartPlayer;
        player?.seek(player.getSnapshot().duration);
      });
      await page.clock.runFor(50);
      expect(await page.evaluate(() => window.coldStartTrace.state)).toEqual(
        source.events.reduce(applyEvent, createInitialState()),
      );
      const settled = await page.evaluate(() => ({ ...window.previewChatRenders }));
      await page.clock.runFor(500);
      expect(await page.evaluate(() => window.previewChatRenders)).toEqual(settled);
      expect(await page.evaluate(() => window.coldStartFailures ?? [])).toEqual([]);
      expect(session.pageErrors).toEqual([]);
      expect(session.diagnostics.filter((item) => item.type === "error")).toEqual([]);
      if (bundled)
        await check(page.getByRole("contentinfo", { name: "Status line" })).toContainText(
          "Real recorded run",
        );
      await writeFile(
        `${chatDirectory}../cold-start-depth/${String(source.path.split("/").at(-1))}-replay.json`,
        JSON.stringify(
          {
            source: source.path,
            duration,
            burstSizes: trace.cursors.map((seq, i) => seq - (trace.cursors[i - 1] ?? 0)),
            timing:
              "Production 1× offsets and 40 ms scheduler; Playwright virtual clock; original timestamps",
            cursors: trace.cursors,
            errors: session.pageErrors,
            console: session.diagnostics,
          },
          null,
          2,
        ) + "\n",
      );
    } finally {
      await session.close();
    }
  },
  60_000,
);

it("measures the first failing synthetic cold-start prefix without masking transport errors", async () => {
  const measurements: unknown[] = [];
  let smallest: number | null = null;
  // Check every shorter prefix, rather than assuming monotonic failure in a binary search.
  for (let count = 0; count <= 80; count++) {
    const events = coldStartStress(count);
    const session = await support.session(chatSizes[0], "dark", "measure=true", events);
    try {
      await session.page.waitForFunction(
        (last) =>
          window.coldStartTrace.state.lastSeq === last || Boolean(window.coldStartFailures?.length),
        events.length,
      );
      const trace = await session.page.evaluate(() => ({
        ...window.coldStartTrace,
        failures: window.coldStartFailures ?? [],
      }));
      measurements.push({
        renderedCursor: await session.page.locator("#root").getAttribute("data-seq"),
        inserted: count,
        events: events.length,
        bytes: Buffer.byteLength(events.map((event) => JSON.stringify(event)).join("\n")),
        beforeLeadView: count + 8,
        cursors: trace.cursors,
        failures: trace.failures,
        deliveries: [...support.deliveries],
        errors: session.pageErrors,
        console: session.diagnostics,
      });
      expect(trace.state).toEqual(events.reduce(applyEvent, createInitialState()));
      expect(trace.cursors).toEqual(events.map((event) => event.seq));
      expect(session.pageErrors).toEqual([]);
      if (trace.failures.some((error) => error.message.includes("Maximum update depth"))) {
        smallest = count;
        break;
      }
      expect(trace.failures).toEqual([]);
    } finally {
      await session.close();
    }
  }
  await writeFile(
    `${chatDirectory}../cold-start-depth/synthetic-minimum.json`,
    JSON.stringify({ smallestInserted: smallest, measurements }, null, 2) + "\n",
  );
  expect(smallest).toBeNull(); // Before the fix, 42 insertions failed at applied 53 / DOM 52.
}, 180_000);

it("measures real SSE Last-Event-ID precedence and the browser's after-based reconnect on a scripted-fake run", async () => {
  const api = await coldStartApi();
  support.api = api.url;
  const session = await support.session(chatSizes[0], "dark", "measure=true", []);
  try {
    const initial = (await session.page.request
      .get(`${api.url}/depth-log`)
      .then((response) => response.json())) as ServerEvent[];
    const cursor = initial.at(-1)?.seq ?? 0;
    await check(session.page.locator("#root")).toHaveAttribute("data-seq", String(cursor));
    const resumed = await readSsePrefix(`${api.url}/events?after=0`, cursor - 1, {
      "Last-Event-ID": "1",
    });
    expect(resumed.events).toEqual(initial.slice(1));
    for (const stream of support.streams) stream.end();
    const started = await session.page.request.post(`${api.url}/agents`, {
      data: {
        task: "Add a docstring to apply_coupon in shop/pricing.py.",
        model: "fake",
        slot: "A",
      },
    });
    expect(started.status()).toBe(201);
    expect(
      (
        await session.page.request.post(`${api.url}/agents`, {
          data: {
            task: "Add a docstring to Inventory.restock in shop/inventory.py.",
            model: "fake",
            slot: "B",
          },
        })
      ).status(),
    ).toBe(201);
    let events: ServerEvent[] = [];
    await check
      .poll(
        async () => {
          events = (await session.page.request
            .get(`${api.url}/depth-log`)
            .then((response) => response.json())) as ServerEvent[];
          return (
            new Set(
              events.flatMap((event) =>
                event.type === "agent.status" && event.payload.state === "ready_for_review"
                  ? [event.payload.agent_id]
                  : [],
              ),
            ).size === 2
          );
        },
        { timeout: 30_000 },
      )
      .toBe(true);
    await check(session.page.locator("#root")).toHaveAttribute(
      "data-seq",
      String(events.at(-1)?.seq),
    );
    const trace = await session.page.evaluate(() => ({
      ...window.coldStartTrace,
      failures: window.coldStartFailures ?? [],
    }));
    expect(trace.cursors).toEqual(events.map((event) => event.seq));
    expect(trace.state).toEqual(events.reduce(applyEvent, createInitialState()));
    const requests = support.deliveries.map(({ after, lastEventId }) => ({
      after,
      lastEventId: lastEventId ?? null,
    }));
    expect(requests).toEqual([
      { after: 0, lastEventId: null },
      { after: cursor, lastEventId: null },
    ]);
    expect(
      trace.failures.filter((error) => error.message.includes("Maximum update depth")),
    ).toEqual([]);
    await check(
      session.page.getByRole("alert").filter({ hasText: /connection|depth/i }),
    ).toHaveCount(0);
    expect(session.pageErrors).toEqual([]);
    const evidence = {
      source: "Fresh LiveRun / ScriptedModel document-apply-coupon / no provider credits",
      events: events.length,
      bytes: Buffer.byteLength(events.map((event) => JSON.stringify(event)).join("\n")),
      beforeLeadView: "no lead.view",
      header: {
        after: 0,
        lastEventId: 1,
        seqs: resumed.events.map((event) => event.seq),
        chunks: resumed.chunks,
      },
      requests,
      deliveries: support.deliveries,
      cursors: trace.cursors,
      failures: trace.failures,
      errors: session.pageErrors,
      console: session.diagnostics,
    };
    await writeFile(
      `${chatDirectory}../cold-start-depth/reconnect.json`,
      JSON.stringify(evidence, null, 2) + "\n",
    );
    await writeFile(
      `${chatDirectory}../cold-start-depth/live-fake.jsonl`,
      events.map((event) => JSON.stringify(event)).join("\n") + "\n",
    );
    for (const route of ["measure=true", "replay=true"]) {
      const cold = await support.session(chatSizes[0], "dark", route, events);
      try {
        if (route === "replay=true") {
          await cold.page.waitForFunction(() => Boolean(window.coldStartPlayer));
          const duration = await cold.page.evaluate(
            () => window.coldStartPlayer?.getSnapshot().duration ?? 0,
          );
          await cold.page.clock.runFor(duration + 40);
        }
        await check(cold.page.locator("#root")).toHaveAttribute("data-seq", String(events.length));
        expect(await cold.page.evaluate(() => window.coldStartTrace.state)).toEqual(
          events.reduce(applyEvent, createInitialState()),
        );
        expect(await cold.page.evaluate(() => window.coldStartFailures ?? [])).toEqual([]);
        expect(cold.pageErrors).toEqual([]);
        expect(cold.diagnostics.filter((item) => item.type === "error")).toEqual([]);
      } finally {
        await cold.close();
      }
    }
  } finally {
    await session.close();
    support.api = undefined;
    await api.close();
  }
}, 60_000);

it("applies all 80 cloned statuses before lead.view and settles without a depth or connection error", async () => {
  const events = coldStartStress(80);
  const session = await support.session(chatSizes[0], "dark", "measure=true", events);
  const { page } = session;
  try {
    await check(page.locator("#root")).toHaveAttribute("data-seq", "91");
    await page.locator(".chat-lead-row").click();
    await check(page.getByRole("button", { name: "Preview app", exact: true })).toBeVisible();
    await check(
      page.getByText("The coupon change is ready to try. Check the shop, then review the change.", {
        exact: true,
      }),
    ).toBeVisible();
    const trace = await page.evaluate(() => window.coldStartTrace);
    expect(trace.cursors).toEqual(events.map((event) => event.seq));
    expect(trace.state).toEqual(events.reduce(applyEvent, createInitialState()));
    expect(trace.state.leadViews["agent-900001"]?.[0]).toMatchObject({
      seq: 89,
      payload: { through_seq: 88 },
    });
    expect(trace.state.leadReplies["agent-900001"]?.[0]).toMatchObject({
      seq: 90,
      payload: { through_seq: 89 },
    });
    expect(support.deliveries.map((delivery) => delivery.after)).toEqual([0]);
    expect(await page.evaluate(() => window.coldStartFailures ?? [])).toEqual([]);
    expect(await page.evaluate(() => window.coldStartAlerts ?? [])).toEqual([]);
    await check(page.getByRole("alert")).toHaveCount(0);
    await check(page.getByRole("contentinfo", { name: "Status line" })).not.toContainText(
      "Connection lost",
    );
    expect(session.pageErrors).toEqual([]);
    expect(session.diagnostics.filter((item) => item.type === "error")).toEqual([]);
    await page.waitForTimeout(100);
    const settled = await page.evaluate(() => ({ ...window.previewChatRenders }));
    await page.waitForTimeout(500);
    expect(await page.evaluate(() => window.previewChatRenders)).toEqual(settled);
    expect(await page.locator("#root").getAttribute("data-seq")).toBe("91");
  } finally {
    await session.close();
  }
});
