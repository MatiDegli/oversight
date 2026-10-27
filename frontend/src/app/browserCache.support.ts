import { basename } from "node:path";
import { fileURLToPath } from "node:url";

// Shared supports run once per test file, so the support's own URL cannot identify its cache.
export function fixtureCacheDir(testPath: string | undefined): string {
  if (!testPath) throw new Error("Browser fixture cache requires the current test file");
  return fileURLToPath(
    new URL(`../../../.shots/${basename(testPath)}-vite-cache`, import.meta.url),
  );
}
