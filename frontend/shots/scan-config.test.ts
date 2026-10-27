// The scan's Vite config swaps the replay site's bundled log for the one in OVERSIGHT_SCAN_LOG, as
// the e2e config does with its own. Resolving the config binds no port and starts no server.
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { test } from "node:test";

import { resolveConfig } from "vite";

const configFile = resolve(import.meta.dirname, "scan.vite.config.ts");

async function aliasOf(log: string | undefined): Promise<{ find: RegExp; replacement: string }[]> {
  const before = process.env.OVERSIGHT_SCAN_LOG;
  if (log === undefined) delete process.env.OVERSIGHT_SCAN_LOG;
  else process.env.OVERSIGHT_SCAN_LOG = log;
  try {
    const config = await resolveConfig(
      { configFile, configLoader: "runner", logLevel: "silent" },
      "serve",
      "replay",
    );
    return config.resolve.alias as { find: RegExp; replacement: string }[];
  } finally {
    if (before === undefined) delete process.env.OVERSIGHT_SCAN_LOG;
    else process.env.OVERSIGHT_SCAN_LOG = before;
  }
}

void test("the app's bundled log import is replaced by the scan's log, whatever relative path it has", async () => {
  const alias = (await aliasOf("/repo/.shots/scan-log.jsonl")).find((entry) =>
    entry.find.test("../../../fixtures/events/handwritten-level1.jsonl?raw"),
  );
  assert.equal(alias?.replacement, "/repo/.shots/scan-log.jsonl?raw");
});

void test("without a log the config refuses to resolve, so the scan never plays the wrong one", async () => {
  await assert.rejects(aliasOf(undefined), /OVERSIGHT_SCAN_LOG names no event log/);
  await assert.rejects(aliasOf(""), /OVERSIGHT_SCAN_LOG names no event log/);
});
