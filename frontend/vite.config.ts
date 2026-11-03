import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import type { Plugin } from "vite";

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
  "/previews",
];

function serverPorts(command: "serve" | "build") {
  // Static builds open no listeners. Port validation belongs to development startup.
  const backendPort = port(
    "OVERSIGHT_PORT",
    command === "serve" ? (process.env.OVERSIGHT_PORT ?? "8710") : "8710",
  );
  const vitePort = port(
    "OVERSIGHT_VITE_PORT",
    command === "serve" ? process.env.OVERSIGHT_VITE_PORT || "5710" : "5710",
  );
  if (vitePort === backendPort)
    throw new Error("OVERSIGHT_VITE_PORT must differ from OVERSIGHT_PORT");
  return { backendPort, vitePort };
}
function previewFrames(command: "serve" | "build", mode: string, backend: number, vite: number) {
  const rawPool = command === "serve" ? process.env.OVERSIGHT_PREVIEW_PORTS || "" : "";
  const pool = rawPool
    ? rawPool.split(",").map((raw) => port("OVERSIGHT_PREVIEW_PORTS", raw.trim()))
    : [];
  if (new Set(pool).size !== pool.length || pool.some((p) => p === backend || p === vite))
    throw new Error("Invalid preview port pool");
  return mode === "replay" || pool.length === 0
    ? "frame-src 'none'"
    : `frame-src ${pool.map((p) => `http://127.0.0.1:${String(p)}`).join(" ")}`;
}
function previewFramePlugin(mode: string): Plugin {
  return {
    name: "preview-frame-policy",
    transformIndexHtml: {
      order: "pre",
      handler: () =>
        mode === "replay"
          ? [
              {
                tag: "meta",
                attrs: { "http-equiv": "Content-Security-Policy", content: "frame-src 'none'" },
                injectTo: "head-prepend",
              },
            ]
          : [],
    },
  };
}
const testProjects = [
  {
    extends: true as const,
    test: {
      name: "unit",
      maxWorkers: 4,
      include: ["src/**/*.test.{ts,tsx}"],
      exclude: ["src/**/*.browser.test.{ts,tsx}"],
    },
  },
  {
    extends: true as const,
    test: {
      name: "browser",
      include: ["src/**/*.browser.test.{ts,tsx}"],
      exclude: ["src/app/preview/PreviewAcceptance.browser.test.ts"],
      maxWorkers: 2,
      testTimeout: 30_000,
    },
  },
  {
    extends: true as const,
    test: {
      // This suite requires the explicit fake/C backend owned by tools.preview_browser.
      name: "preview-acceptance",
      include: ["src/app/preview/PreviewAcceptance.browser.test.ts"],
      maxWorkers: 2,
      testTimeout: 30_000,
    },
  },
];
export default defineConfig(({ command, mode }) => {
  const { backendPort, vitePort } = serverPorts(command);
  const backend = `http://127.0.0.1:${String(backendPort)}`;
  return {
    ...(cacheDir === undefined || cacheDir === "" ? {} : { cacheDir }),
    plugins: [
      previewFramePlugin(mode),
      noticesPlugin(),
      recordedReplayPlugin(),
      react(),
      tailwindcss(),
    ],
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
      headers: { "Content-Security-Policy": previewFrames(command, mode, backendPort, vitePort) },
      host: "127.0.0.1",
      port: vitePort,
      strictPort: true,
      proxy: Object.fromEntries(apiPaths.map((path) => [path, { target: backend }])),
    },
    // Unit checks need no browser; only the browser project launches Playwright.
    test: { projects: testProjects },
  };
});
