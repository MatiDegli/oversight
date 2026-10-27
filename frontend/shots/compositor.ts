// node shots/compositor.ts --label before|after [--out ../.shots]
// Uses the current working tree's Vite dev build, the shell fixture's review camera, and no API.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import type { Browser, CDPSession } from "@playwright/test";
import { createServer } from "vite";
import type {} from "../src/app/shell.browser.fixture";
import type { GraphProbeHost } from "../src/graph";
import { compositorTime } from "./compositor-trace.ts";
import type { TraceEvent } from "./compositor-trace.ts";
import { installMotionClock, moveCamera } from "./compositor-motion.ts";

const { values } = parseArgs({ options: { label: { type: "string" }, out: { type: "string" } } });
if (!["before", "after"].includes(values.label ?? ""))
  throw new Error("--label before|after required");
const label = values.label ?? "before";
const root = fileURLToPath(new URL("../../", import.meta.url));
const out = resolve(values.out ?? resolve(root, ".shots"));

async function finishTrace(client: CDPSession) {
  const complete = new Promise<{ stream?: string }>((done) =>
    client.once("Tracing.tracingComplete", done),
  );
  await client.send("Tracing.end");
  const { stream } = await complete;
  if (!stream) throw new Error("Chromium returned no trace stream");
  const chunks: Buffer[] = [];
  let eof = false;
  while (!eof) {
    const part = await client.send("IO.read", { handle: stream });
    chunks.push(Buffer.from(part.data, part.base64Encoded ? "base64" : "utf8"));
    eof = part.eof;
  }
  await client.send("IO.close", { handle: stream });
  return Buffer.concat(chunks);
}

async function sample(browser: Browser, url: string, html: string, index: number) {
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    reducedMotion: "no-preference",
  });
  try {
    await installMotionClock(page);
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.shellTest);
    await page.evaluate(() => {
      window.shellTest.mount();
      window.shellTest.update();
      window.shellTest.open("review");
    });
    await page.evaluate(() => document.fonts.ready);
    await page.locator('[aria-label="Possibly affected"]').waitFor();
    await page.waitForFunction(
      () =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.animating() === false,
    );
    // Warm the shader and settle the editor before tracing; a fresh page repeats the same camera.
    await page.locator(".monaco-editor .view-lines").first().waitFor();
    const client = await page.context().newCDPSession(page);
    await client.send("Tracing.start", {
      categories: "toplevel,cc,viz,gpu,devtools.timeline",
      transferMode: "ReturnAsStream",
    });
    const motion = await moveCamera(page);
    const trace = await finishTrace(client);
    await writeFile(resolve(out, `compositor-${label}-${String(index)}.trace.json`), trace);
    const parsed = JSON.parse(trace.toString()) as { traceEvents: TraceEvent[] };
    return { ...compositorTime(parsed.traceEvents), motion };
  } finally {
    await page.close();
  }
}

async function main() {
  await mkdir(out, { recursive: true });
  const server = await createServer({
    root: resolve(root, "frontend"),
    cacheDir: resolve(out, "compositor-cache"),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  let browser: Browser | undefined;
  try {
    const html = await server.transformIndexHtml(
      "/",
      '<html><body><script type="module">import "/src/theme/index.css"; import { shellTest } from "/src/app/shell.browser.fixture.tsx"; window.shellTest = shellTest;</script></body></html>',
    );
    await server.listen();
    browser = await chromium.launch();
    const info = (await (await browser.newBrowserCDPSession()).send("SystemInfo.getInfo")) as {
      gpu: unknown;
    };
    const samples: Awaited<ReturnType<typeof sample>>[] = [];
    for (let index = 1; index <= 3; index++) {
      const result = await sample(browser, server.resolvedUrls?.local[0] ?? "", html, index);
      if (samples[0])
        assert.deepEqual(
          result.motion,
          samples[0].motion,
          "Camera and field draw counts must repeat",
        );
      samples.push(result);
      console.log(`compositor ${label} ${String(index)}: ${result.totalMs.toFixed(3)} ms`);
    }
    const medianMs = samples.map((sample) => sample.totalMs).sort((a, b) => a - b)[1];
    const report = {
      label,
      commit: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(),
      build: "Vite dev shell fixture",
      chromium: browser.version(),
      gpu: info.gpu,
      metric:
        "Union of top-level RunTask wall durations on Compositor and VizCompositorThread, summed across threads; not GPU execution time",
      medianMs,
      samples,
    };
    await writeFile(
      resolve(out, `compositor-${label}.json`),
      JSON.stringify(report, null, 2) + "\n",
    );
    if (label === "after") {
      const before = JSON.parse(
        await readFile(resolve(out, "compositor-before.json"), "utf8"),
      ) as typeof report;
      assert.deepEqual(
        samples[0]?.motion,
        before.samples[0]?.motion,
        "A/B camera and field motion must match",
      );
      await writeFile(
        resolve(out, "compositor-ab.json"),
        JSON.stringify({ before, after: report }, null, 2) + "\n",
      );
    }
  } finally {
    await browser?.close();
    await server.close();
  }
}

await main();
