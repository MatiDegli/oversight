import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { verifyShellCaptures } from "./shell-captures.ts";

void test("shell evidence requires both watch and focus at 1920x1080", async () => {
  const directory = await mkdtemp(join(tmpdir(), "shell-captures-"));
  // Only the PNG header is relevant to the artifact presence/size check.
  const png = Buffer.alloc(33);
  png.set(Buffer.from("89504e470d0a1a0a", "hex"));
  png.writeUInt32BE(13, 8);
  png.write("IHDR", 12);
  png.writeUInt32BE(1920, 16);
  png.writeUInt32BE(1080, 20);
  try {
    await assert.rejects(verifyShellCaptures(directory), /watch-dark.png/);
    await writeFile(join(directory, "watch-dark.png"), png);
    await assert.rejects(verifyShellCaptures(directory), /focus-dark.png/);
    await writeFile(join(directory, "focus-dark.png"), png);
    await verifyShellCaptures(directory);
    for (const [offset, dimension] of [
      [16, "width"],
      [20, "height"],
    ] as const) {
      const wrongSize = Buffer.from(png);
      wrongSize.writeUInt32BE(900, offset);
      await writeFile(join(directory, "focus-dark.png"), wrongSize);
      await assert.rejects(
        verifyShellCaptures(directory),
        new RegExp(`focus-dark.png: recording ${dimension}`),
      );
    }
    await writeFile(join(directory, "focus-dark.png"), "not a PNG");
    await assert.rejects(verifyShellCaptures(directory), /truncated PNG/);
    await writeFile(join(directory, "focus-dark.png"), Buffer.alloc(33));
    await assert.rejects(verifyShellCaptures(directory), /PNG signature/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
