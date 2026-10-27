// The public static replay build plus two test seams: the L1 fixture replaces its fallback log;
// /level2 replays a validated backend/fake recording with HTTP commands intercepted by Playwright.
// The normal replay page retains its disabled commands. No backend starts for either scenario.
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { defineConfig, mergeConfig } from "vite";

import base from "../vite.config.ts";
import { E2E_LOG } from "./log.ts";

const log = fileURLToPath(new URL(`../../fixtures/events/${E2E_LOG}`, import.meta.url));

const root = fileURLToPath(new URL("../../", import.meta.url));
const transport = fileURLToPath(new URL("./transport.ts", import.meta.url));

export default defineConfig((env) =>
  mergeConfig(base(env), {
    plugins: [
      {
        name: "oversight-level2-fixture",
        buildStart() {
          // Validate the exact exported bytes and fake-only receipt before bundling the fixture.
          execFileSync(
            "uv",
            [
              "run",
              "--frozen",
              "python",
              "tools/validate_recording.py",
              "--fake-fixture",
              "fixtures/events/scripted-level2.jsonl",
            ],
            {
              cwd: root,
              stdio: "pipe",
              timeout: 60_000,
            },
          );
        },
      },
    ],
    resolve: {
      // The import in src/transport/index.ts, whatever relative path it uses.
      alias: [
        { find: /^\.\.\/transport$/, replacement: transport },
        { find: /^.*\/handwritten-level1\.jsonl\?raw$/, replacement: `${log}?raw` },
      ],
    },
  }),
);
