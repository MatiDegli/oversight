import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { afterEach, beforeEach, expect, it } from "vitest";
import { captureNames, writeCaptureMatrix } from "./planFlowEvidence.testSupport";

it("preserves the coordinator-owned team rail browser suite byte for byte", async () => {
  // The label-only reservation lift covers chatAgents/chatLeads, not this suite.
  // Pin the approved origin/main bytes independently of local branch history.
  const source = await readFile(new URL("./teamRail.browser.test.ts", import.meta.url));
  expect(createHash("sha256").update(source).digest("hex")).toBe(
    "89221da1c6d76fb7e31945cd9e7fd7692b513e8b9e3ea60182b95a90f08534b7",
  );
});

let root: URL;
beforeEach(async () => {
  root = pathToFileURL(`${await mkdtemp(join(tmpdir(), "plan-flow-evidence-"))}/`);
  for (const name of captureNames) {
    const [scene, width, theme] = name.split("-");
    for (const suffix of [".png", "-end.png"])
      // File-level validation needs the PNG signature; no browser rendering is simulated here.
      await writeFile(new URL(`${name}${suffix}`, root), Buffer.from("89504e470d0a1a0a", "hex"));
    await writeFile(
      new URL(`${name}.json`, root),
      JSON.stringify({
        scene,
        theme,
        width: Number(width),
        documentWidth: Number(width),
        provenance: "Synthetic scripted event scene, rendered by production components",
        selectors: { ".plan-flow": [{ clientWidth: 336, scrollWidth: 336 }] },
      }),
    );
  }
});
afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

it("stages all 52 PNGs and 26 measurement files with content hashes", async () => {
  const matrix = await writeCaptureMatrix(root);
  expect(matrix.captures).toHaveLength(26);
  expect(new Set(matrix.captures.map((capture) => capture.name)).size).toBe(26);
  const files = matrix.captures.flatMap((capture) => capture.files);
  expect(files.filter((file) => file.name.endsWith(".png"))).toHaveLength(52);
  expect(files.filter((file) => file.name.endsWith(".json"))).toHaveLength(26);
  for (const file of files) expect(file.sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(JSON.parse(await readFile(new URL("matrix.json", root), "utf8"))).toEqual(matrix);
});

it.each(["proposed-1280-dark.png", "phone-390-day-end.png", "phone-390-day.json"])(
  "refuses delivery when %s is absent",
  async (name) => {
    // A failed refresh must also invalidate a formerly complete delivery index.
    await writeCaptureMatrix(root);
    await rm(new URL(name, root));
    await expect(writeCaptureMatrix(root)).rejects.toThrow("ENOENT");
    await expect(readFile(new URL("matrix.json", root))).rejects.toThrow("ENOENT");
  },
);

it("refuses a non-PNG capture", async () => {
  await writeFile(new URL("proposed-1280-dark.png", root), "missing screenshot");
  await expect(writeCaptureMatrix(root)).rejects.toThrow("Invalid PNG");
});

it.each([
  { theme: undefined },
  { theme: "day" },
  { scene: "running" },
  { width: 390 },
  { documentWidth: 1281 },
  { selectors: { ".plan-flow": [] } },
  { selectors: { ".plan-flow": [{ clientWidth: 336, scrollWidth: 337 }] } },
])("refuses mismatched or overflowing measurements: %j", async (patch) => {
  const path = new URL("proposed-1280-dark.json", root);
  const data = JSON.parse(await readFile(path, "utf8")) as Record<string, unknown>;
  await writeFile(path, JSON.stringify({ ...data, ...patch }));
  await expect(writeCaptureMatrix(root)).rejects.toThrow("Invalid scene measurements");
});
