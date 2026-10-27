// One test over the static replay site: level 1's definition of done, played from the hand-written
// log, in the order a person sees it. The page is the replay build as judges open it, served by
// `vite preview` with no backend, so every step waits on the page (the graph probe and the DOM) and
// follows the log's own pace; nothing here sleeps for a fixed time. `make e2e` runs it.
import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { ServerEvent } from "@contracts";

import type { GraphProbeHost } from "../src/graph";

const LOG_NAME = "handwritten-level1.jsonl";
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
const drawn = snapshot.nodes.filter((node) => !node.is_test).map((node) => node.id);
const afterDelta = [
  ...drawn.filter((id) => !delta.nodes_removed.includes(id)),
  ...delta.nodes_added.filter((node) => !node.is_test).map((node) => node.id),
];
const slot = payloads("agent.status")[0]?.slot;
if (slot === undefined) throw new Error(`${LOG_NAME} has no agent status`);
// What the person sees light up, in order: each focus event names the nodes it lights.
const lighting = payloads("agent.focus").map(
  ({ mode, node_ids }) => `${slot} ${mode}:${node_ids.toSorted().join(",")}`,
);
// The lines the agent's edit added: what the diff must show.
const originalLines = (update.file.original ?? "").split("\n");
const addedLines = (update.file.modified ?? "")
  .split("\n")
  .filter((line) => line.trim() !== "" && !originalLines.includes(line));
if (addedLines.length === 0) throw new Error(`${LOG_NAME}: the edit adds no line`);
const changedNode = update.graph.nodes_modified[0];
if (changedNode === undefined) throw new Error(`${LOG_NAME}: the edit names no node`);

// The backend's paths: the replay site must never ask for one (vite.config.ts proxies the same).
const API_PATHS = ["/agents", "/changes", "/events", "/files", "/graph", "/health", "/impact"];

interface Sample {
  /** Node ids by lit state, such as "read" or "edit" for agent A and for agent B. */
  lit: Record<string, string[]>;
  panels: string[];
}
type Recorded = Window & { e2eSamples: Sample[] };

/**
 * Runs in the page from its first frame: on every frame, records what is lit and which side panel
 * is open, whenever either changed. A state that lasts a second or two is then never missed
 * because the test was busy with something else, and a panel that opened by itself is on record.
 */
function installRecorder() {
  const samples: Sample[] = [];
  Object.defineProperty(window, "e2eSamples", { value: samples });
  let last = "";
  const sample = () => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    if (probe) {
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
      const next = { lit, panels };
      const key = JSON.stringify(next);
      if (key !== last) {
        last = key;
        samples.push(next);
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

test("level 1 over replay: watch, live diff, ready pill, review, recorded decision, graph update", async ({
  page,
  baseURL,
}) => {
  const problems: string[] = [];
  const requested: string[] = [];
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(message.text());
  });
  page.on("request", (request) => requested.push(request.url()));
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

  await test.step("the Ready for review pill appears, and nothing opens by itself", async () => {
    await expect(card.getByRole("status")).toHaveText("Ready for review");
    await expect(page.getByRole("status", { name: "Review queue" })).toContainText(
      "1 change ready for review",
    );
    await expect(page.getByRole("complementary", { name: "Live diff panel" })).toHaveCount(0);
    await expect(page.getByRole("complementary", { name: "Review panel" })).toHaveCount(0);
    // The change waits as a dashed pending node, in the agent's color.
    expect(await nodeIds(page, `node.pending-${slot}`)).toEqual(
      proposal.graph.nodes_modified.toSorted(),
    );
  });

  const review = page.getByRole("complementary", { name: "Review panel" });
  await test.step("review opens from the card; Approve and Reject only show the recorded run", async () => {
    // The log decides two seconds after the proposal, so what the decision area shows before that
    // is read first; the inline diff, which stays after the decision, is read below.
    await card.getByRole("button", { name: "Review change" }).click();
    await expect(review).toBeVisible();
    await expect(review.getByRole("button", { name: /Approve and merge/ })).toBeDisabled();
    await expect(review.getByRole("button", { name: "Reject" })).toBeDisabled();
    await expect(review.getByText("Recorded run: nothing here sends a decision.")).toBeVisible();
    await expect(review.getByRole("heading", { name: proposal.task })).toBeVisible();
    await expect(review.getByRole("region", { name: `Diff of ${update.file.path}` })).toBeVisible();
    await expect(review.locator(".monaco-diff-editor .line-insert").first()).toBeVisible();
    await expect(review.getByText("Read-only · Inline diff")).toBeVisible();
  });

  await test.step("the recorded decision applies and the graph updates from graph.delta", async () => {
    const decision = review.getByRole("region", { name: "Decision" });
    await expect(
      decision.getByText("Recorded run: this is the decision that was made."),
    ).toBeVisible();
    await expect(decision.getByText("Approved and merged into main")).toBeVisible();
    await expect(decision.getByText(resolution.merge_commit?.slice(0, 7) ?? "")).toBeVisible();
    await expect(review.getByText("Approved", { exact: true })).toBeVisible();
    // graph.delta: the top bar moves to the merge commit and the node set follows the delta.
    await expect(page.locator(`header span[title="${delta.commit}"]`)).toBeVisible();
    await expect(page.locator(`header span[title="${snapshot.commit}"]`)).toHaveCount(0);
    await expect.poll(() => nodeIds(page, "node")).toEqual(afterDelta.toSorted());
    // Decided: no pending node and no light is left, and the whole log is applied.
    await expect.poll(() => nodeIds(page, "node.pending")).toEqual([]);
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
