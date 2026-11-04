import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import {
  expect as check,
  type Browser,
  type Frame,
  type Page,
  type Response,
} from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../../browserLaunch.support";

const sandbox = "allow-scripts allow-same-origin allow-forms";
const external = "document.body.dataset.external = 'ran';";
const probes = `<script>document.body.dataset.inline = 'ran';</script>
<script src="/probe.js"></script>
<button id="handler" onclick="document.body.dataset.handler = 'ran'">Run handler probe</button>
<p id="probe-complete">Fixture probes parsed</p>`;
const control = `<!doctype html><html><body><h1>Candidate shop control</h1>${probes}</body></html>`;

// The primary document goes through the real shop, render, respond and header emission.
// Only fixture bodies gain probes. Header suppression is confined to the explicit control.
const adapterFixture = `
import json, socket, sys
sys.path.insert(0, sys.argv[1])
from oversight import preview_adapter as adapter
parent, root, probes, external = sys.argv[2:]
class FixtureHandler(adapter.Handler):
    def dispatch(self):
        if self.command == "GET" and self.path == "/probe.js":
            self.respond(200, external.encode(), "text/javascript")
        elif self.command == "GET" and self.path == "/control":
            self.render((1, 1), "")
        else:
            super().dispatch()
    def respond(self, code, body, content_type):
        if code == 200 and content_type.startswith("text/html"):
            body = body.replace(b"</body>", probes.encode() + b"</body>")
        super().respond(code, body, content_type)
    def send_header(self, name, value):
        if self.path == "/control" and name.lower() == "content-security-policy":
            return
        super().send_header(name, value)
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    config = adapter.configuration([
        "--listener-fd", str(listener.fileno()), "--port", str(port),
        "--worktree", root, "--ready-nonce", "a" * 64, "--oversight-origin", parent])
    with adapter.Server(config, adapter.Shop(config.worktree), listener) as server:
        server.RequestHandlerClass = FixtureHandler
        server.timeout = 1
        print(json.dumps({"port": port}), flush=True)
        # An orphaned fixture cannot leave a permanent listener, even if Node dies.
        import time
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            server.handle_request()
`;
const entry = `
import React from "react";
import { createRoot } from "react-dom/client";
import { PreviewPanel } from "/src/app/preview/PreviewPanel.tsx";
import { fakeStatus } from "/src/app/preview/previewFake.ts";
const view = { visible: true, status: fakeStatus("ready"), owned: true,
  pending: false, uncertain: false, notice: null,
  frameUrl: new URLSearchParams(location.search).get("frame") };
const noop = () => {};
const controller = { snapshot: () => view, subscribe: () => noop,
  see: noop, observe: noop, refresh: noop, close: noop, navigate: noop,
  setIdentity: noop, dispose: noop };
createRoot(document.getElementById("root")).render(<PreviewPanel controller={controller} />);
`;

function bound<T>(promise: Promise<T>, milliseconds: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error("Sandbox fixture timed out"));
    }, milliseconds);
    void promise.then(resolve, reject).finally(() => {
      clearTimeout(timer);
    });
  });
}
let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let child: ChildProcess | undefined;
let parent = "";
let shop = "";

async function startAdapter() {
  const process = spawn(
    "python3",
    [
      "-u",
      "-c",
      adapterFixture,
      fileURLToPath(new URL("../../../../backend/src/", import.meta.url)),
      parent,
      fileURLToPath(new URL("../../../../demo-repo/", import.meta.url)),
      probes,
      external,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  child = process;
  let output = "",
    errors = "";
  process.stderr.on("data", (chunk: Buffer) => {
    errors += chunk.toString();
  });
  const port = await bound(
    new Promise<number>((resolve, reject) => {
      process.once("error", reject);
      process.once("exit", (code) => {
        reject(new Error(`Adapter exited ${String(code)}: ${errors}`));
      });
      process.stdout.on("data", (chunk: Buffer) => {
        output += chunk.toString();
        if (!output.includes("\n")) return;
        try {
          resolve((JSON.parse(output.trim()) as { port: number }).port);
        } catch (error) {
          reject(new Error("Invalid adapter readiness", { cause: error }));
        }
      });
    }),
    10_000,
  );
  shop = `http://127.0.0.1:${String(port)}`;
}
function running(process: ChildProcess) {
  return process.exitCode === null && process.signalCode === null;
}
async function dispose() {
  try {
    await bound(browser?.close() ?? Promise.resolve(), 5_000);
  } finally {
    try {
      await bound(server?.close() ?? Promise.resolve(), 5_000);
    } finally {
      if (child && child.exitCode === null && child.signalCode === null) {
        const exited = once(child, "exit");
        child.kill("SIGTERM");
        try {
          await bound(exited, 3_000);
        } finally {
          if (running(child)) {
            child.kill("SIGKILL");
            await bound(exited, 3_000);
          }
        }
      }
    }
  }
}
beforeAll(async () => {
  try {
    server = await createServer({
      appType: "custom",
      root: fileURLToPath(new URL("../../../", import.meta.url)),
      cacheDir: fileURLToPath(
        new URL("../../../../.shots/preview-sandbox/vite-cache", import.meta.url),
      ),
      plugins: [
        {
          name: "sandbox-panel-fixture",
          resolveId: (id) => (id === "/sandbox-entry.tsx" ? id : undefined),
          load: (id) => (id === "/sandbox-entry.tsx" ? entry : undefined),
        },
      ],
      optimizeDeps: { entries: ["src/app/preview/PreviewPanel.tsx"] },
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    const html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html><body><div id="root" style="height:100vh"></div><script type="module" src="/sandbox-entry.tsx"></script></body></html>',
    );
    server.middlewares.use((request, response, next) => {
      const path = request.url?.split("?")[0];
      if (path === "/same-control" || path === "/probe.js") {
        response.removeHeader("Content-Security-Policy");
        response.setHeader("Content-Type", path === "/probe.js" ? "text/javascript" : "text/html");
        response.end(path === "/probe.js" ? external : control);
      } else if (path === "/") {
        response.setHeader("Content-Type", "text/html");
        response.setHeader("Content-Security-Policy", `frame-src 'self' ${shop}`);
        response.end(html);
      } else next();
    });
    await bound(server.listen(), 10_000);
    const url = server.resolvedUrls?.local[0];
    if (!url) throw new Error("Missing sandbox parent URL");
    // The real adapter requires Host 127.0.0.1; use localhost for the parent.
    parent = new URL(url).origin.replace("127.0.0.1", "localhost");
    await startAdapter();
    browser = await bound(launchBrowser(), 10_000);
  } catch (error) {
    await dispose();
    throw error;
  }
}, 45_000);
afterAll(dispose, 20_000);

async function withPanel(
  url: string,
  test: (page: Page, frame: Frame, response: Response) => Promise<void>,
) {
  if (!browser) throw new Error("Missing sandbox browser");
  const context = await browser.newContext();
  context.setDefaultTimeout(5_000);
  try {
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const [documentResponse] = await Promise.all([
      page.waitForResponse(
        (reply) => reply.url() === url && reply.request().resourceType() === "document",
      ),
      page.goto(`${parent}/?frame=${encodeURIComponent(url)}`),
    ]);
    await check(page.locator("iframe")).toHaveAttribute("sandbox", sandbox);
    expect(documentResponse.status()).toBe(200);
    const frame = documentResponse.frame();
    expect(frame.parentFrame()).toBe(page.mainFrame());
    expect(frame.url()).toBe(url);
    await frame.waitForLoadState("load", { timeout: 5_000 });
    expect(await frame.evaluate(() => document.readyState)).toBe("complete");
    await check(frame.locator("#probe-complete")).toHaveText("Fixture probes parsed");
    await test(page, frame, documentResponse);
    expect(errors).toEqual([]);
  } finally {
    await bound(context.close(), 5_000);
  }
}
async function probeMarkers(frame: Frame) {
  // A real user event triggers the served handler; evaluation only reads its effects.
  await frame.locator("#handler").click();
  return frame.evaluate(() => ({
    inline: document.body.dataset.inline ?? null,
    handler: document.body.dataset.handler ?? null,
    external: document.body.dataset.external ?? null,
  }));
}
async function originProbe(frame: Frame) {
  // Privileged instrumentation can inspect a script-disabled document. It does not
  // establish script execution: only the served probes' observable markers do that.
  return frame.evaluate(() => {
    let access: string, storage: string;
    try {
      access = window.parent.document.querySelector("iframe") ? "parent DOM reached" : "no iframe";
    } catch (error) {
      access = (error as DOMException).name;
    }
    try {
      storage = localStorage.length >= 0 ? "available" : "invalid";
    } catch (error) {
      storage = (error as DOMException).name;
    }
    return { access, storage, effectiveOrigin: window.origin };
  });
}
it("retains the served shop CSP, blocks every script probe and keeps an opaque origin", async () => {
  await withPanel(`${shop}/`, async (page, frame, response) => {
    expect(response.status()).toBe(200);
    expect
      .soft(response.headers()["content-security-policy"])
      .toBe(
        `default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors ${parent}; sandbox allow-forms`,
      );
    expect(response.headers()["referrer-policy"]).toBe("no-referrer");
    expect(response.headers()["cache-control"]).toBe("no-store");
    expect(response.headers()["content-type"]).toBe("text/html; charset=utf-8");
    await check(frame.getByRole("heading", { name: "Candidate shop", exact: true })).toBeVisible();
    await check(frame.locator("output")).toHaveAttribute("data-total-cents", "1500");
    expect(new URL(frame.url()).origin).not.toBe(new URL(page.url()).origin);
    expect.soft(await probeMarkers(frame)).toEqual({ inline: null, handler: null, external: null });
    expect(await originProbe(frame)).toEqual({
      access: "SecurityError",
      storage: "SecurityError",
      effectiveOrigin: "null",
    });
  });
});
it("executes the same inline, handler and external probes without CSP across origins", async () => {
  await withPanel(`${shop}/control`, async (page, frame, response) => {
    expect(response.headers()["content-security-policy"]).toBeUndefined();
    expect(new URL(frame.url()).origin).not.toBe(new URL(page.url()).origin);
    expect(await probeMarkers(frame)).toEqual({ inline: "ran", handler: "ran", external: "ran" });
    expect(await originProbe(frame)).toEqual({
      access: "SecurityError",
      storage: "available",
      effectiveOrigin: shop,
    });
  });
});
it("lets the DOM-access probe succeed in a same-origin control without CSP sandbox", async () => {
  await withPanel(`${parent}/same-control`, async (page, frame, response) => {
    expect(response.headers()["content-security-policy"]).toBeUndefined();
    expect(new URL(frame.url()).origin).toBe(new URL(page.url()).origin);
    expect(await probeMarkers(frame)).toEqual({ inline: "ran", handler: "ran", external: "ran" });
    expect(await originProbe(frame)).toEqual({
      access: "parent DOM reached",
      storage: "available",
      effectiveOrigin: parent,
    });
  });
});
