// The Vite config of the Impeccable scan's server (run.sh): the app's own config with one change,
// that the replay site plays the log named in OVERSIGHT_SCAN_LOG where it would play the hand-written
// level 1 log (the same swap as e2e/vite.config.ts). It runs in replay mode, which opens no
// connection after load, so the scan's navigation can finish (see scan-log.ts).
import { defineConfig, mergeConfig } from "vite";

import base from "../vite.config.ts";

const log = process.env.OVERSIGHT_SCAN_LOG;
if (log === undefined || log === "") throw new Error("OVERSIGHT_SCAN_LOG names no event log");

export default defineConfig((env) =>
  mergeConfig(base(env), {
    resolve: {
      // The import in src/transport/index.ts, whatever relative path it uses.
      alias: [{ find: /^.*\/handwritten-level1\.jsonl\?raw$/, replacement: `${log}?raw` }],
    },
  }),
);
