import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { publishTeamColorsEvidence, type TeamCapture } from "./teamColorsEvidence.testSupport";

const sourceSha = "d797d5462fd9074c4091bb8a491c8355d82464ad";

it("publishes provenance beside every capture and includes it in the reviewer copy", async () => {
  const directory = await mkdtemp(join(tmpdir(), "team-colors-evidence-"));
  try {
    const captures: TeamCapture[] = [];
    for (const width of [1920, 1280])
      for (const theme of ["dark", "day"] as const)
        for (const [scene, fixtureCut] of Object.entries({
          "graph-busy": 19,
          "graph-rest": 37,
          inbox: 21,
          "seventh-team": 58,
          "chat-flat": 19,
        })) {
          const filename = `${scene}${width === 1280 ? "-1280" : ""}-${theme}.png`;
          captures.push({ filename, width, theme, fixtureCut });
          await writeFile(join(directory, filename), `image bytes for ${filename}`);
        }
    await publishTeamColorsEvidence(directory, sourceSha, captures);
    const manifest = await readFile(join(directory, "PROVENANCE.md"), "utf8");
    expect(manifest).toContain("Handwritten events over Boltons d31d821");
    expect(manifest).toContain(sourceSha);
    expect(manifest).toContain("fixtures/events/handwritten-team-colors.jsonl");
    expect(manifest).toContain("graph.snapshot at seq 2");
    expect(manifest).toContain("TEAM_COLORS_SHOTS=1");
    expect(manifest).toContain("vitest related --run --maxWorkers 2");
    expect(manifest).not.toMatch(/recorded/i);
    expect(await readdir(join(directory, "reviewer-copy"))).toHaveLength(21);
    expect(await readFile(join(directory, "reviewer-copy/PROVENANCE.md"), "utf8")).toBe(manifest);
    for (const capture of captures) {
      const bytes = await readFile(join(directory, capture.filename));
      const digest = createHash("sha256").update(bytes).digest("hex");
      expect(manifest).toContain(
        `| [${capture.filename}](${capture.filename}) | ${String(capture.fixtureCut)} | ${String(capture.fixtureCut + 1)} | ${String(capture.width)}×${capture.width === 1280 ? "800" : "1080"} | ${capture.theme} | \`${digest}\` |`,
      );
      expect(await readFile(join(directory, "reviewer-copy", capture.filename))).toEqual(bytes);
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
