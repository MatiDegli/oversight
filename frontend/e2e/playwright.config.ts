// The browser run over replay: `make e2e` (run.sh) builds the static replay site into dist/replay,
// serves it with `vite preview` on a free port (OVERSIGHT_E2E_PORT) that it owns and stops, and runs
// this folder over it. No backend is started, and no web server is configured here: a server
// Playwright starts lives in a process group of its own and outlives a run that `timeout` ended.
import { defineConfig } from "@playwright/test";
import { browserLaunchOptions } from "../src/browserLaunch.support";

const port = process.env.OVERSIGHT_E2E_PORT;
if (port === undefined || port === "")
  throw new Error("OVERSIGHT_E2E_PORT is not set: run `make e2e`, which serves the site first");
const origin = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  // The recorded log plays about twenty seconds; the test follows it and waits on the page.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: process.env.CI === "true" ? [["list"], ["html", { open: "never" }]] : [["list"]],
  outputDir: "../test-results",
  use: {
    baseURL: origin,
    viewport: { width: 1920, height: 1080 },
    trace: "retain-on-failure",
    // Specs checking the field opt in with browserLaunchOptions({ webGL: true });
    // the helper also honors OVERSIGHT_E2E_SOFTWARE_RENDERING for those specs.
    launchOptions: browserLaunchOptions(),
  },
});
