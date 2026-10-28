import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";

/** Verify the shell hand-back's artifacts independently of the capture process. */
export async function verifyShellCaptures(directory: string): Promise<void> {
  for (const name of ["watch-dark.png", "focus-dark.png"]) {
    const png = await readFile(join(directory, name));
    assert.ok(png.length >= 33, `${name}: truncated PNG`);
    assert.equal(png.subarray(0, 8).toString("hex"), "89504e470d0a1a0a", `${name}: PNG signature`);
    assert.equal(png.readUInt32BE(8), 13, `${name}: IHDR length`);
    assert.equal(png.subarray(12, 16).toString(), "IHDR", `${name}: IHDR missing`);
    assert.equal(png.readUInt32BE(16), 1920, `${name}: recording width`);
    assert.equal(png.readUInt32BE(20), 1080, `${name}: recording height`);
  }
}

// Run after make shots: node frontend/shots/shell-captures.ts [capture directory]
if (import.meta.main)
  await verifyShellCaptures(process.argv[2] ?? resolve(import.meta.dirname, "../../.shots"));
