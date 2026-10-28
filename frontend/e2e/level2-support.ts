import { expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";
import type { SaveFile } from "@contracts";
import type { GraphProbeHost } from "../src/graph";

import { events, firstUpdate, hash } from "./level2-log";

export async function interceptCommands(page: Page) {
  const sent: { path: string; method: string; body: unknown }[] = [];
  const unexpected: string[] = [];
  const saved = new Map<string, string>();
  const key = (path: string | null, agent: string | null) => JSON.stringify([path, agent]);
  await page.route(
    /\/(agents|changes|files|graph|events|health|impact|plans|merge-mode|tree|history|queue|leads)(\/|\?|$)/,
    async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const method = req.method();
      if (url.pathname === "/files" && method === "GET") {
        const seq = Number(await page.locator("#root").getAttribute("data-seq"));
        const agent = url.searchParams.get("agent");
        const updates = events.filter(
          (e) =>
            e.type === "change.updated" &&
            e.seq <= seq &&
            (agent === null || e.payload.agent_id === agent) &&
            e.payload.file.path === url.searchParams.get("path"),
        );
        const latest = updates.at(-1);
        const content =
          saved.get(key(url.searchParams.get("path"), agent)) ??
          (latest?.type === "change.updated"
            ? (latest.payload.file.modified ?? "")
            : (firstUpdate.file.original ?? ""));
        await route.fulfill({
          json: {
            path: url.searchParams.get("path"),
            agent_id: agent,
            content,
            hash: hash(content),
          },
        });
        return;
      }
      const body: unknown = req.postData() === null ? null : req.postDataJSON();
      sent.push({ path: url.pathname, method, body });
      if (url.pathname === "/files" && method === "PUT") {
        const save = body as SaveFile;
        saved.set(key(save.path, save.agent_id ?? null), save.content);
        await route.fulfill({ json: { path: save.path, hash: hash(save.content) } });
      } else if (
        method === "POST" &&
        /\/(request-changes|approve|reject|stop)$/.test(url.pathname)
      ) {
        await route.fulfill({ status: 202 });
      } else {
        unexpected.push(`${method} ${url.pathname}`);
        await route.abort();
      }
    },
  );
  return { sent, unexpected };
}

export const appearance = (page: Page, selector: string) =>
  page.evaluate((selector) => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    if (!probe) throw new Error("Graph not mounted");
    return probe.appearance(selector);
  }, selector);

export async function append(page: Page, panel: Locator, text: string) {
  await panel.locator(":is(.native-edit-context, textarea.inputarea)").last().focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(text);
}

export async function settledFrame(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((done) =>
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            done();
          }),
        ),
      ),
  );
}

export async function tapNode(page: Page, id: string) {
  const graph = page.getByLabel("Repository graph", { exact: true });
  await expect
    .poll(() => graph.evaluate((host: GraphProbeHost) => host.oversightGraph?.animating()))
    .toBe(false);
  const point = await graph.evaluate(
    (host: GraphProbeHost, id) => host.oversightGraph?.nodeCenters().find((n) => n.id === id),
    id,
  );
  if (!point) throw new Error(`No visible node ${id}`);
  await graph.click({ position: { x: point.x, y: point.y } });
}
