import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { noticesPlugin } from "./tools/notices.ts";
import { recordedReplayPlugin } from "./tools/recorded-replay.ts";

// One origin in development: Vite serves the UI and forwards the API and the SSE stream to FastAPI.
// The proxy streams responses as they arrive, so SSE events are not buffered.
function port(name: string, raw: string): number {
  const value = Number(raw);
  if (!/^[0-9]+$/.test(raw) || !Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`${name} must be an integer in 1-65535`);
  }
  return value;
}
// `make shots` keeps Vite's cache under .shots/ through this variable, so it never shares state with
// `make dev`. Without it, Vite's default (node_modules/.vite) applies.
const cacheDir = process.env.OVERSIGHT_VITE_CACHE_DIR;
const apiPaths = [
  "/agents",
  "/changes",
  "/events",
  "/files",
  "/grants",
  "/graph",
  "/health",
  "/impact",
  "/merge-mode",
  "/plans",
  "/tree",
  "/history",
  "/queue",
  "/leads",
  "/dictation",
  "/usage",
];

export default defineConfig(({ command }) => {
  // A static build opens no listeners. Port validation belongs to development startup.
  const backendPort = port(
    "OVERSIGHT_PORT",
    command === "serve" ? (process.env.OVERSIGHT_PORT ?? "8710") : "8710",
  );
  const vitePort = port(
    "OVERSIGHT_VITE_PORT",
    command === "serve" ? process.env.OVERSIGHT_VITE_PORT || "5710" : "5710",
  );
  if (vitePort === backendPort) {
    throw new Error("OVERSIGHT_VITE_PORT must differ from OVERSIGHT_PORT");
  }
  const backend = `http://127.0.0.1:${String(backendPort)}`;
  return {
    ...(cacheDir === undefined || cacheDir === "" ? {} : { cacheDir }),
    plugins: [noticesPlugin(), recordedReplayPlugin(), react(), tailwindcss()],
    worker: { format: "es" },
    build: { manifest: true },
    resolve: {
      alias: {
        "@contracts": fileURLToPath(
          new URL("../contracts/generated/contracts.ts", import.meta.url),
        ),
      },
    },
    server: {
      host: "127.0.0.1",
      port: vitePort,
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
            maxWorkers: 4,
            include: ["src/**/*.test.{ts,tsx}"],
            exclude: ["src/**/*.browser.test.{ts,tsx}"],
          },
        },
        {
          extends: true,
          test: {
            name: "browser",
            include: ["src/**/*.browser.test.{ts,tsx}"],
            maxWorkers: 2,
            testTimeout: 30_000,
          },
        },
      ],
    },
  };
});
