import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { recordedReplayPlugin } from "./tools/recorded-replay.ts";

// One origin in development: Vite serves the UI and forwards the API and the SSE stream to FastAPI.
// The proxy streams responses as they arrive, so SSE events are not buffered.
const backend = `http://127.0.0.1:${process.env.OVERSIGHT_PORT ?? "8710"}`;
// `make shots` keeps Vite's cache under .shots/ through this variable, so it never shares state with
// `make dev`. Without it, Vite's default (node_modules/.vite) applies.
const cacheDir = process.env.OVERSIGHT_VITE_CACHE_DIR;
const apiPaths = [
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

export default defineConfig({
  ...(cacheDir === undefined || cacheDir === "" ? {} : { cacheDir }),
  plugins: [recordedReplayPlugin(), react(), tailwindcss()],
  worker: { format: "es" },
  resolve: {
    alias: {
      "@contracts": fileURLToPath(new URL("../contracts/generated/contracts.ts", import.meta.url)),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5710,
    strictPort: true,
    proxy: Object.fromEntries(apiPaths.map((path) => [path, { target: backend }])),
  },
  // `pnpm test` runs the unit project, which needs no browser, so make check and CI stay runnable
  // without one. `pnpm test:browser` runs the tests that drive Monaco in Playwright's Chromium.
  test: {
    projects: [
      {
        extends: true,
        test: {
          name: "unit",
          include: ["src/**/*.test.{ts,tsx}"],
          exclude: ["src/**/*.browser.test.{ts,tsx}"],
        },
      },
      { extends: true, test: { name: "browser", include: ["src/**/*.browser.test.{ts,tsx}"] } },
    ],
  },
});
