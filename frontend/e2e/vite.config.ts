// The build of `make e2e`: the app's own config (../vite.config.ts) with one change, that the
// replay site bundles the log named in log.ts where it would bundle the hand-written level 1 log.
// Everything else is the site judges open.
import { fileURLToPath } from "node:url";

import { mergeConfig } from "vite";

import base from "../vite.config.ts";
import { E2E_LOG } from "./log.ts";

const log = fileURLToPath(new URL(`../../fixtures/events/${E2E_LOG}`, import.meta.url));

export default mergeConfig(base, {
  resolve: {
    // The import in src/transport/index.ts, whatever relative path it uses.
    alias: [{ find: /^.*\/handwritten-level1\.jsonl\?raw$/, replacement: `${log}?raw` }],
  },
});
