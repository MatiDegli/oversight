import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { expect, test } from "@playwright/test";
import type { BrowserContext } from "@playwright/test";
import type { ServerEvent } from "@contracts";

const replayPath = "/oversight/";
const build = new URL("../dist/replay/oversight/", import.meta.url);
// Actual emitted files, never API names, extensions, directory prefixes or SPA fallback responses.
const staticPaths = new Set(
  readdirSync(build, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => {
      const file = pathToFileURL(join(entry.parentPath, entry.name));
      return replayPath + file.pathname.slice(build.pathname.length);
    }),
);
staticPaths.add(replayPath); // The static server's index.html directory alias.
const events = readFileSync(
  new URL("../../fixtures/events/recorded-demo.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const editedFile = events.find((event) => event.type === "change.updated")?.payload.file.path;
if (!editedFile) throw new Error("The real recording must include an edited file");

interface NetworkAttempt {
  method: string;
  type: string;
  url: string;
}

async function watchStaticBuild(context: BrowserContext, baseURL: string | undefined) {
  const origin = new URL(baseURL ?? "").origin;
  const requests: NetworkAttempt[] = [];
  const unexpected: NetworkAttempt[] = [];
  const allowed = ({ method, type, url: address }: NetworkAttempt) => {
    const url = new URL(address);
    // Monaco's in-memory workers and data resources do not contact a server.
    if (url.protocol === "blob:" || url.protocol === "data:") return true;
    return (
      (method === "GET" || method === "HEAD") &&
      type !== "eventsource" &&
      url.origin === origin &&
      staticPaths.has(url.pathname)
    );
  };
  // Context scope includes navigation, frames, workers and requests that fail before a response.
  context.on("request", (request) => {
    const attempt = {
      method: request.method(),
      type: request.resourceType(),
      url: request.url(),
    };
    requests.push(attempt);
    if (!allowed(attempt)) unexpected.push(attempt);
  });
  // Record first, then abort: even a regressed build cannot contact an external service here.
  await context.route("**/*", async (route) => {
    const request = route.request();
    if (allowed({ method: request.method(), type: request.resourceType(), url: request.url() }))
      await route.continue();
    else await route.abort("blockedbyclient");
  });
  // WebSocket handshakes are not HTTP request events. No static file opens a socket.
  await context.routeWebSocket("**/*", async (socket) => {
    const attempt = { method: "GET", type: "websocket", url: socket.url() };
    requests.push(attempt);
    unexpected.push(attempt);
    await socket.close();
  });
  return {
    requests,
    unexpected,
    assertClean: () => {
      expect(unexpected, "Requests outside the static build").toEqual([]);
    },
  };
}

// Service workers could hide requests from routing; a fresh context must not install one.
test.use({ serviceWorkers: "block", reducedMotion: "reduce" });

test("the judge's replay journey uses only emitted static files", async ({
  context,
  page,
  baseURL,
}) => {
  const network = await watchStaticBuild(context, baseURL);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(replayPath);
    await expect(page.getByRole("contentinfo", { name: "Status line" })).toContainText(
      "Real recorded run",
    );
    const controls = page.getByRole("region", { name: "Replay controls" });
    await expect(controls).toBeVisible();
    // Explicit selection works with either initial view, including a future Chat default.
    const selector = page.getByRole("group", { name: "Workspace view" });
    for (const view of ["Chat", "Oversight"] as const) {
      const button = selector.getByRole("button", { name: view, exact: true });
      await button.click();
      await expect(button).toHaveAttribute("aria-pressed", "true");
      if (view === "Chat")
        await expect(page.getByRole("complementary", { name: "Where they work" })).toBeVisible();
      else await expect(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Files", exact: true }).click();
      await expect(page.getByRole("complementary", { name: "Files sidebar" })).toContainText(
        "Recorded files",
      );
      await page.getByRole("button", { name: "Close sidebar", exact: true }).click();
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await expect(page.getByRole("tab", { name: "Settings", exact: true })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(
        page.getByText("Setting the merge mode is not available in a replay."),
      ).toBeVisible();
      await page.getByRole("button", { name: "Close tab Settings", exact: true }).click();
    }
    // Restart actually plays every event, rather than seeking directly to the last frame.
    await controls.getByRole("button", { name: "Restart", exact: true }).click();
    await controls.getByRole("button", { name: "4×", exact: true }).click();
    await expect(controls).toContainText("End of recording", { timeout: 60_000 });
    await expect(page.locator("#root")).toHaveAttribute("data-seq", String(events.at(-1)?.seq));
    await page.getByRole("button", { name: "Files", exact: true }).click();
    const sidebar = page.getByRole("complementary", { name: "Files sidebar" });
    for (const folder of editedFile.split("/").slice(0, -1))
      await sidebar.getByRole("treeitem", { name: folder, exact: true }).click();
    await sidebar
      .getByRole("treeitem", { name: editedFile.split("/").at(-1), exact: true })
      .click();
    const file = page.getByRole("complementary", { name: "File view" });
    await expect(file).toContainText("Read-only · Recorded file");
    await expect(file.locator(".monaco-editor .view-lines").first()).toContainText("def ");
    expect(errors).toEqual([]);
    expect(network.requests.some((request) => request.type === "script")).toBe(true);
  } finally {
    network.assertClean();
  }
});

for (const type of ["fetch", "xhr", "eventsource", "websocket"] as const) {
  test(`the detector rejects a synthetic ${type} backend attempt`, async ({
    context,
    page,
    baseURL,
  }) => {
    const network = await watchStaticBuild(context, baseURL);
    await page.goto(replayPath);
    await expect(page.getByRole("region", { name: "Replay controls" })).toBeVisible();
    network.assertClean();
    // Deliberately absent from the build, under its own origin/base: Vite's SPA fallback would
    // return index.html. Neither a successful HTTP response nor a matching prefix can admit it.
    const url = new URL(`${replayPath}__synthetic_backend__`, baseURL);
    if (type === "websocket") url.protocol = "ws:";
    await page.evaluate(
      ({ type, url }) => {
        switch (type) {
          case "fetch":
            void fetch(url).catch(() => {});
            break;
          case "xhr": {
            const xhr = new XMLHttpRequest();
            xhr.open("GET", url);
            xhr.send();
            break;
          }
          case "eventsource": {
            const source = new EventSource(url);
            source.onerror = () => {
              source.close();
            };
            break;
          }
          case "websocket":
            new WebSocket(url);
            break;
        }
      },
      { type, url: url.href },
    );
    await expect.poll(() => network.unexpected).toEqual([{ method: "GET", type, url: url.href }]);
    // Exercise the very same assertion as the journey; a control passes only if it fails.
    expect(() => {
      network.assertClean();
    }).toThrow("Requests outside the static build");
  });
}
