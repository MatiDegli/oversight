import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

import type { Manifest } from "vite";

/** Count the entry and all static imports once, excluding deferred editor chunks and workers. */
export function startupFiles(manifest: Manifest, entry = "index.html"): string[] {
  const visited = new Set<string>();
  const files = new Set<string>();
  function visit(key: string) {
    if (visited.has(key)) return;
    visited.add(key);
    const chunk = manifest[key];
    if (!chunk) throw new Error(`Missing startup chunk: ${key}`);
    if (chunk.file.endsWith(".js")) files.add(chunk.file);
    for (const dependency of chunk.imports ?? []) visit(dependency);
  }
  visit(entry);
  return [...files].sort();
}

/** Gzip each response separately, as a static host would, using Node's default gzip settings. */
export function measureStartupJs(directory: string) {
  const manifest = JSON.parse(
    readFileSync(join(directory, ".vite/manifest.json"), "utf8"),
  ) as Manifest;
  const files = startupFiles(manifest);
  let raw = 0;
  let gzip = 0;
  for (const file of files) {
    const bytes = readFileSync(join(directory, file));
    raw += bytes.length;
    gzip += gzipSync(bytes).length;
  }
  return { files, raw, gzip };
}
