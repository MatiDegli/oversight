// One test over the static replay site: level 1's definition of done, played from the hand-written
// log (log.ts: its variant whose change adds a function), in the order a person sees it. The page is
// the replay build as judges open it, served by `vite preview` with no backend. Every step waits on
// the page (the graph probe and the DOM); nothing sleeps for a fixed time. The log's pace would
// still leave windows (the live diff is offered only until the proposal, the decision buttons only
// until the decision, two seconds later), so the page holds the replay at those two events until the
// assertions that need the window are done. `make e2e` runs it.
import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { ServerEvent } from "@contracts";

import type { GraphProbeHost } from "../src/graph";
import { E2E_LOG } from "./log.ts";

const LOG_NAME = E2E_LOG;
const events = readFileSync(new URL(`../../fixtures/events/${LOG_NAME}`, import.meta.url), "utf8")
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

type Payload<T extends ServerEvent["type"]> = Extract<ServerEvent, { type: T }>["payload"];

/** The payloads of one event type, in log order. */
function payloads<T extends ServerEvent["type"]>(type: T): Payload<T>[] {
  const found: Payload<T>[] = [];
  for (const event of events) if (event.type === type) found.push(event.payload as Payload<T>);
  return found;
}

const snapshot = payloads("graph.snapshot")[0];
const delta = payloads("graph.delta")[0];
const update = payloads("change.updated")[0];
const proposal = payloads("change.proposed")[0];
const resolution = payloads("change.resolved")[0];
const steps = payloads("agent.step");
if (!snapshot || !delta || !update || !proposal || !resolution)
  throw new Error(`${LOG_NAME} must hold a snapshot, a delta, an edit, a proposal and a decision`);

// The graph hides test nodes by default; the snapshot's other nodes are what the page draws.
const ancestors = new Map(snapshot.nodes.map((node) => [node.id, node]));
const representedBy = (id: string) => {
  let node = ancestors.get(id);
  let visible = id;
  while (node?.parent_id) {
    node = ancestors.get(node.parent_id);
    if (node?.kind === "class") visible = node.id;
  }
  return visible;
};
const drawn = snapshot.nodes
  .filter((node) => !node.is_test && representedBy(node.id) === node.id)
  .map((node) => node.id);
const afterDelta = [
  ...drawn.filter((id) => !delta.nodes_removed.includes(id)),
  ...delta.nodes_added.filter((node) => !node.is_test).map((node) => node.id),
];
/** The wording review takes from the log's source (src/transport/index.ts). */
const REPLAY_OF = "Replay of a hand-written demo fixture";
const slot = payloads("agent.status")[0]?.slot;
if (slot === undefined) throw new Error(`${LOG_NAME} has no agent status`);
// What the person sees light up, in order: each focus event names the nodes it lights.
const lighting = payloads("agent.focus").map(
  ({ mode, node_ids }) =>
    `${slot} ${mode}:${[...new Set(node_ids.map(representedBy))].sort().join(",")}`,
);
const changedNode = update.graph.nodes_modified[0];
if (changedNode === undefined) throw new Error(`${LOG_NAME}: the edit names no node`);
// The lines the agent's edit added inside the node it changed: what the diff must show. Monaco
// draws only the lines in view, and the node it changed is at the top of the file.
const span = delta.nodes_changed.find((node) => node.id === changedNode);
if (!span) throw new Error(`${LOG_NAME}: the delta does not carry ${changedNode}`);
const originalLines = (update.file.original ?? "").split("\n");
const addedLines = (update.file.modified ?? "")
  .split("\n")
  .filter(
    (line, index) =>
      index + 1 >= span.start_line &&
      index + 1 <= span.end_line &&
      line.trim() !== "" &&
      !originalLines.includes(line),
  );
if (addedLines.length === 0)
  throw new Error(`${LOG_NAME}: the edit adds no line in ${changedNode}`);
// The change adds a function: it waits on the graph as a pending new node, and the merge's delta
// moves it into main. Without it, a delta that changes lines only would show nothing on the page.
const addedByChange = proposal.graph.nodes_added.filter((node) => !node.is_test).map((n) => n.id);
if (addedByChange.length === 0) throw new Error(`${LOG_NAME}: the change adds no node`);
if (!delta.nodes_added.some((node) => addedByChange.includes(node.id)))
  throw new Error(`${LOG_NAME}: the merge delta must add what the change added`);
const beforeMerge = [...drawn, ...addedByChange];

// The replay is held before these two events: the proposal and the decision.
const proposedAt = events.findIndex((event) => event.type === "change.proposed");
const resolvedAt = events.findIndex((event) => event.type === "change.resolved");
const atProposal = holdBefore(proposedAt, 1771);
const atDecision = holdBefore(resolvedAt, 1777);

/** Pixels of the agent's color a lit node must show: its border draws 490 to 840 at this viewport. */
const MIN_PAINTED_PIXELS = 150;

// The backend's paths: the replay site must never ask for one (vite.config.ts proxies the same).
const API_PATHS = [
  "/agents",
  "/changes",
  "/events",
  "/files",
  "/graph",
  "/health",
  "/impact",
  "/merge-mode",
  "/plans",
];

/**
 * A pause in the replay before one event. The replay waits min(gap, 2000) ms before each event, and
 * several waits are exactly 2000; so the page's Date.parse answers a gap of `delay` ms for the held
 * event's timestamp, a value no other timer uses, and the page's setTimeout keeps the one timer of
 * that length until the test releases it. Nothing else in the page is paused.
 */
interface Hold {
  ts: string;
  after: string;
  delay: number;
}
function holdBefore(index: number, delay: number): Hold {
  const event = events[index];
  const before = events[index - 1];
  if (!event || !before)
    throw new Error(`${LOG_NAME}: no event to hold before at ${String(index)}`);
  return { ts: event.ts, after: before.ts, delay };
}
type Holding = Window & { e2eHolds: { held: number[]; release(delay: number): void } };

function installHolds(holds: Hold[]) {
  const parse = Date.parse.bind(Date);
  const moved = new Map(holds.map(({ ts, after, delay }) => [ts, parse(after) + delay]));
  Date.parse = (text: string) => moved.get(text) ?? parse(text);
  const delays = new Set(holds.map(({ delay }) => delay));
  const held: number[] = [];
  const waiting = new Map<number, () => void>();
  const start = window.setTimeout.bind(window);
  window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
    if (
      typeof handler === "function" &&
      delay !== undefined &&
      delays.has(delay) &&
      !waiting.has(delay)
    ) {
      waiting.set(delay, () => {
        start(handler, 0, ...args);
      });
      held.push(delay);
      return 0;
    }
    return start(handler, delay, ...args);
  }) as typeof window.setTimeout;
  Object.defineProperty(window, "e2eHolds", {
    value: {
      held,
      release(delay: number) {
        waiting.get(delay)?.();
      },
    },
  });
}

/** Waits until the replay is at the hold, with every earlier event applied and none after it. */
async function holdEngaged(page: Page, hold: Hold, index: number) {
  await expect
    .poll(() => page.evaluate(() => (window as unknown as Holding).e2eHolds.held))
    .toContain(hold.delay);
  await expect(page.locator("#root")).toHaveAttribute("data-seq", String(events[index - 1]?.seq));
}
const release = (page: Page, hold: Hold) =>
  page.evaluate((delay) => {
    (window as unknown as Holding).e2eHolds.release(delay);
  }, hold.delay);

/** Node ids by lit state, such as "A read" or "B edit", and how much of each agent's color is drawn. */
interface Sample {
  lit: Record<string, string[]>;
  panels: string[];
  /** For each lit node: the pixels around it in its own agent's token hue, the most seen. */
  paint: Record<string, number>;
  /** The pixels around lit nodes in the other agent's hue, the most seen; none should be. */
  stray: number;
}
type Recorded = Window & { e2eSamples: Sample[] };

/**
 * Runs in the page from its first frame: on every frame, records what is lit and which side panel
 * is open, whenever either changed. A state that lasts a second or two is then never missed
 * because the test was busy with something else, and a panel that opened by itself is on record.
 * It also reads the graph's canvas around each lit node, so the record says what was drawn, in
 * the agent's token color (--agent-a, --agent-b), and not only which class the node carries.
 */
function installRecorder() {
  const samples: Sample[] = [];
  Object.defineProperty(window, "e2eSamples", { value: samples });
  // The document does not exist yet when this script first runs: read the page's style on a frame.
  const root = () => getComputedStyle(document.documentElement);
  const surface = document.createElement("canvas");
  const pixels = surface.getContext("2d", { willReadFrequently: true });
  if (!pixels) throw new Error("No 2d canvas to read the graph with");
  const hueOf = (r: number, g: number, b: number) => {
    const high = Math.max(r, g, b);
    const chroma = high - Math.min(r, g, b);
    // Only saturated pixels have a hue worth comparing: the borders, not the dark fills and text.
    if (chroma < 60) return -1;
    const sector =
      high === r
        ? ((g - b) / chroma + 6) % 6
        : high === g
          ? (b - r) / chroma + 2
          : (r - g) / chroma + 4;
    return sector * 60;
  };
  const tokenHue = (name: string) => {
    pixels.fillStyle = root().getPropertyValue(name).trim();
    pixels.fillRect(0, 0, 1, 1);
    const [r = 0, g = 0, b = 0] = pixels.getImageData(0, 0, 1, 1).data;
    return hueOf(r, g, b);
  };
  // Read once the graph is up: the stylesheet has not loaded when this script first runs.
  let hues = { A: -1, B: -1 };
  const near = (hue: number, target: number) => {
    const gap = Math.abs(hue - target);
    return Math.min(gap, 360 - gap) <= 14;
  };
  /** How many pixels in the box around (x, y) have each agent's hue, over the graph's canvases. */
  const count = (
    host: HTMLElement,
    x: number,
    y: number,
    halfWidth: number,
    halfHeight: number,
  ) => {
    const left = Math.max(0, Math.round(x - halfWidth));
    const top = Math.max(0, Math.round(y - halfHeight));
    const width = Math.max(1, Math.round(halfWidth * 2));
    const height = Math.max(1, Math.round(halfHeight * 2));
    surface.width = width;
    surface.height = height;
    for (const layer of host.querySelectorAll("canvas")) {
      const scale = layer.width / host.clientWidth;
      pixels.drawImage(
        layer,
        left * scale,
        top * scale,
        width * scale,
        height * scale,
        0,
        0,
        width,
        height,
      );
    }
    const found = { A: 0, B: 0 };
    const { data } = pixels.getImageData(0, 0, width, height);
    for (let at = 0; at < data.length; at += 4) {
      const hue = hueOf(data[at] ?? 0, data[at + 1] ?? 0, data[at + 2] ?? 0);
      if (hue < 0) continue;
      if (near(hue, hues.A)) found.A += 1;
      else if (near(hue, hues.B)) found.B += 1;
    }
    return found;
  };
  let font = 0;
  const row = { width: 0, height: 0 };
  let last = "";
  const sample = () => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const probe = host?.oversightGraph;
    if (host && probe) {
      if (font === 0) {
        font = Number.parseFloat(root().getPropertyValue("--body-size"));
        row.width = Number.parseFloat(root().getPropertyValue("--node-width"));
        row.height = Number.parseFloat(root().getPropertyValue("--node-height"));
        hues = { A: tokenHue("--agent-a"), B: tokenHue("--agent-b") };
        if (!(font > 0) || hues.A < 0 || hues.B < 0 || Math.abs(hues.A - hues.B) < 90)
          throw new Error(
            "The tokens must give a font size and two distinct, saturated agent colors",
          );
      }
      const lit: Record<string, string[]> = {};
      for (const slot of ["A", "B"])
        for (const mode of ["read", "edit"])
          lit[`${slot} ${mode}`] = probe
            .nodeCenters(`node.focus-${slot}-${mode}`)
            .map(({ id }) => id)
            .sort();
      const panels = ["Live diff panel", "Review panel"].filter(
        (label) => document.querySelector(`aside[aria-label="${label}"]`) !== null,
      );
      const key = JSON.stringify({ lit, panels });
      if (key !== last) {
        last = key;
        samples.push({ lit, panels, paint: {}, stray: 0 });
      }
      const current = samples[samples.length - 1];
      if (current) {
        // A row is the theme's node width by node height, 282 by 46; a few pixels more take in its
        // border, where an agent's color is drawn.
        const halfWidth = (row.width / 2) * probe.zoom() + 4;
        const halfHeight = (row.height / 2) * probe.zoom() + 4;
        for (const slot of ["A", "B"] as const)
          for (const mode of ["read", "edit"])
            for (const { id, x, y } of probe.nodeCenters(`node.focus-${slot}-${mode}`)) {
              // The canvas starts at 100% and is panned: a node beyond its edge draws nothing here.
              if (x < 0 || y < 0 || x > host.clientWidth || y > host.clientHeight) continue;
              const found = count(host, x, y, halfWidth, halfHeight);
              current.paint[id] = Math.max(current.paint[id] ?? 0, found[slot]);
              current.stray = Math.max(current.stray, found[slot === "A" ? "B" : "A"]);
            }
      }
    }
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
}

const recorded = (page: Page) => page.evaluate(() => (window as unknown as Recorded).e2eSamples);

/** The graph's read-only probe: the ids of the nodes a selector matches, sorted. */
function nodeIds(page: Page, selector: string) {
  return page.evaluate((wanted) => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    if (!probe) throw new Error("The graph is not mounted");
    return probe
      .nodeCenters(wanted)
      .map(({ id }) => id)
      .sort();
  }, selector);
}

test("level 1 over replay: watch, live diff, ready pill, review, logged decision, graph update", async ({
  page,
  baseURL,
}) => {
  const problems: string[] = [];
  const requested: string[] = [];
  page.on("pageerror", (error) => {
    console.log("PAGEERROR", error.message);
    problems.push(error.message);
  });
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(message.text());
  });
  page.on("request", (request) => requested.push(request.url()));
  await page.addInitScript(installHolds, [atProposal, atDecision]);
  await page.addInitScript(installRecorder);

  await test.step("the graph renders the snapshot's nodes", async () => {
    await page.goto("/");
    await expect.poll(() => nodeIds(page, "node")).toEqual(drawn.toSorted());
    const placed = await page.evaluate(() => {
      const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
      return { size: host?.clientWidth ?? 0, positions: host?.oversightGraph?.positions() ?? {} };
    });
    expect(placed.size).toBeGreaterThan(0);
    const leaves = Object.values(placed.positions);
    expect(leaves.length).toBeGreaterThan(0);
    expect(leaves.every(({ x, y }) => Number.isFinite(x) && Number.isFinite(y))).toBe(true);
    // The top bar names the commit the snapshot drew; the delta will move it.
    await expect(page.locator(`header span[title="${snapshot.commit}"]`)).toBeVisible();
    // The site says what it plays: a hand-written log is never labeled as a real recording.
    const label = page.getByText(/^Replay ·/);
    await expect(label).toBeVisible();
    await expect(label).toContainText(/hand-written/i);
    await expect(label).not.toContainText(/real recorded run/i);
    // So does the new-agent box on the demo path: it never calls the fixture a recording.
    const newAgent = page.getByRole("region", { name: "New agent" });
    await expect(newAgent).toContainText(
      "Replay of a hand-written demo fixture: agents cannot be started here.",
    );
    await expect(newAgent).not.toContainText(/recorded run/i);
  });

  const card = page.getByRole("article", { name: `Agent ${slot}` });
  const stepList = card.getByRole("list", { name: /steps, newest first/ });
  await test.step("the agent's card shows its status and steps", async () => {
    const first = steps[0];
    if (!first) throw new Error(`${LOG_NAME} has no steps`);
    await expect(card.getByText("Working", { exact: true })).toBeVisible();
    await expect(card).toContainText(payloads("agent.status")[0]?.task ?? "");
    await expect(stepList.getByRole("listitem").filter({ hasText: first.summary })).toBeVisible();
    await expect(card.getByText(/Step\s*\d+\s*of\s*30/)).toBeVisible();
  });

  await test.step("read nodes light, then edited nodes, in the agent's color", async () => {
    // The recorder saw each light on its frame: agent A's only (another agent's would carry its
    // letter), the reads before the edit. The edit stays lit until the agent proposes.
    const lit = async () =>
      (await recorded(page)).flatMap(({ lit: byState }) =>
        Object.entries(byState)
          .filter(([, ids]) => ids.length > 0)
          .map(([name, ids]) => `${name}:${ids.join(",")}`),
      );
    await expect.poll(lit).toEqual(lighting);
    expect(await nodeIds(page, `node.focus-${slot}-edit`)).toEqual([changedNode]);
    // The canvas drew it: around every lit node there is agent A's token color, and none of B's.
    // (Read lights are the only color on the graph while they last; a node's class alone is not.)
    const painted = async () => {
      const lights = (await recorded(page)).filter((sample) =>
        Object.values(sample.lit).some((ids) => ids.length > 0),
      );
      // Every lit node on the canvas shows the color; one beyond its edge is not on it to be read.
      const least = lights.flatMap((sample) =>
        Object.values(sample.lit).flatMap((ids) =>
          ids.filter((id) => id in sample.paint).map((id) => sample.paint[id] ?? 0),
        ),
      );
      // The node the agent edits is the hub, in the starting view: it is always one of them.
      const edited = lights.some((sample) => changedNode in sample.paint);
      return { lights: lights.length, least: edited ? Math.min(...least) : -1 };
    };
    await expect
      .poll(async () => (await painted()).least)
      .toBeGreaterThanOrEqual(MIN_PAINTED_PIXELS);
    expect((await painted()).lights).toBe(lighting.length);
    const strays = (await recorded(page)).map((sample) => sample.stray);
    expect(Math.max(...strays)).toBe(0);
  });

  await test.step("the live diff opens read-only from the card, with Monaco's diff", async () => {
    await card.getByRole("button", { name: "Live diff" }).click();
    const panel = page.getByRole("complementary", { name: "Live diff panel" });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("heading", { name: update.file.path })).toBeVisible();
    await expect(panel.getByText("Read-only · Inline diff")).toBeVisible();
    await expect(panel.locator(".monaco-diff-editor .line-insert").first()).toBeVisible();
    // Read-only is Monaco's own state: typing into the diff changes nothing (an editable one takes it).
    const lines = panel.locator(".editor.modified .view-lines");
    for (const line of addedLines) await expect(lines).toContainText(line.trim());
    await panel
      .locator(".editor.modified .view-line")
      .filter({ hasText: "def compute_price" })
      .click();
    await page.keyboard.type("typed_by_the_test = 1");
    await page.evaluate(
      () =>
        new Promise<void>((done) =>
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              done();
            });
          }),
        ),
    );
    expect((await lines.allTextContents()).join("\n")).not.toContain("typed_by_the_test");
    await panel.getByRole("button", { name: "Close live diff" }).click();
    await expect(panel).toHaveCount(0);
  });

  await test.step("the replay waits before the proposal, so the live diff had its whole window", async () => {
    await holdEngaged(page, atProposal, proposedAt);
    await expect(card.getByText("Working", { exact: true })).toBeVisible();
    await expect(card.getByRole("status")).toHaveCount(0);
    await expect(page.getByRole("status", { name: "Review queue" })).not.toContainText("ready");
    await release(page, atProposal);
  });

  await test.step("the Ready for review pill appears, and nothing opens by itself", async () => {
    await expect(card.getByRole("status")).toHaveText("Ready for review");
    await expect(page.getByRole("status", { name: "Review queue" })).toContainText(
      "1 change ready for review",
    );
    await expect(page.getByRole("complementary", { name: "Live diff panel" })).toHaveCount(0);
    await expect(page.getByRole("complementary", { name: "Review panel" })).toHaveCount(0);
    // The change waits as dashed pending nodes, in the agent's color: the node it changed and the
    // one it added, which main does not have yet.
    expect(await nodeIds(page, `node.pending-${slot}`)).toEqual(
      [...proposal.graph.nodes_modified, ...addedByChange].toSorted(),
    );
    expect(await nodeIds(page, `node.pending-new-${slot}`)).toEqual(addedByChange.toSorted());
    expect(await nodeIds(page, "node")).toEqual(beforeMerge.toSorted());
  });

  const review = page.getByRole("complementary", { name: "Review panel" });
  await test.step("review opens from the card; Approve and Reject only show the replay", async () => {
    // The replay is held before the decision, so the decision area is read as a person meets it
    // before the decision arrives, whatever the machine's pace. The inline diff stays after it.
    await holdEngaged(page, atDecision, resolvedAt);
    await card.getByRole("button", { name: "Review change" }).click();
    await expect(review).toBeVisible();
    await expect(review.getByRole("button", { name: /Approve and merge/ })).toBeDisabled();
    await expect(review.getByRole("button", { name: "Reject" })).toBeDisabled();
    // A hand-written log never calls itself a recording: the wording comes from the log's source,
    // as the label at the top left does.
    await expect(review.getByText(`${REPLAY_OF}: nothing here sends a decision.`)).toBeVisible();
    await expect(review).not.toContainText(/recorded run/i);
    await expect(review.getByRole("heading", { name: proposal.task })).toBeVisible();
    await expect(review.getByRole("region", { name: `Diff of ${update.file.path}` })).toBeVisible();
    await expect(review.locator(".monaco-diff-editor .line-insert").first()).toBeVisible();
    await expect(review.getByText("Read-only · Inline diff")).toBeVisible();
    await release(page, atDecision);
  });

  await test.step("the logged decision applies and the graph updates from graph.delta", async () => {
    const decision = review.getByRole("region", { name: "Decision" });
    await expect(
      decision.getByText(`${REPLAY_OF}: this is the decision the log holds.`),
    ).toBeVisible();
    await expect(review).not.toContainText(/recorded run/i);
    await expect(decision.getByText("Approved and merged into main")).toBeVisible();
    await expect(decision.getByText(resolution.merge_commit?.slice(0, 7) ?? "")).toBeVisible();
    await expect(review.getByText("Approved", { exact: true })).toBeVisible();
    // graph.delta: the top bar moves to the merge commit and the node set follows the delta, which
    // adds the function the change added: it stops being pending and main has it.
    await expect(page.locator(`header span[title="${delta.commit}"]`)).toBeVisible();
    await expect(page.locator(`header span[title="${snapshot.commit}"]`)).toHaveCount(0);
    await expect.poll(() => nodeIds(page, "node")).toEqual(afterDelta.toSorted());
    // Decided: no pending node and no light is left, and the whole log is applied.
    await expect.poll(() => nodeIds(page, "node.pending")).toEqual([]);
    expect(await nodeIds(page, `node.pending-new-${slot}`)).toEqual([]);
    expect(afterDelta).toEqual(expect.arrayContaining(addedByChange));
    await expect(page.locator("#root")).toHaveAttribute("data-seq", String(events.length));
    await page.getByRole("button", { name: "Close review" }).click();
    await expect(review).toHaveCount(0);
    await expect(card.getByText("Done", { exact: true })).toBeVisible();
    await expect(stepList.getByRole("listitem")).toHaveCount(steps.length);
  });

  await test.step("only the person opened panels, and the site never asked for a backend", async () => {
    // Opened by a click each, in this order, and nothing else: no panel came up on an event.
    const panels = (await recorded(page)).map((sample) => sample.panels.join("+"));
    const changes = panels.filter((name, index) => name !== panels[index - 1]);
    expect(changes).toEqual(["", "Live diff panel", "", "Review panel", ""]);
    const origin = new URL(baseURL ?? "").origin;
    const foreign = requested.filter((url) => new URL(url).origin !== origin);
    const backend = requested.filter((url) =>
      API_PATHS.some((path) => new URL(url).pathname.startsWith(path)),
    );
    expect({ foreign, backend, problems }).toEqual({ foreign: [], backend: [], problems: [] });
  });
});
