// The browser run over replay: `make e2e` builds the static replay site into dist/replay, then
// Playwright serves it with `vite preview` on a free port (OVERSIGHT_E2E_PORT) and runs this folder.
// No backend is started.
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

const port = Number(process.env.OVERSIGHT_E2E_PORT ?? "4310");
const origin = `http://127.0.0.1:${String(port)}`;

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  // The recorded log plays about twenty seconds; the test follows it and waits on the page.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [["list"]],
  outputDir: "../test-results",
  use: { baseURL: origin, viewport: { width: 1920, height: 1080 }, trace: "retain-on-failure" },
  webServer: {
    command: `./node_modules/.bin/vite preview --host 127.0.0.1 --port ${String(port)} --strictPort --outDir dist/replay --configLoader runner`,
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    url: origin,
    reuseExistingServer: false,
    timeout: 60_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
  },
});
