import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

export interface TeamCapture {
  filename: string;
  width: number;
  theme: "dark" | "day";
  fixtureCut: number;
}

export async function publishTeamColorsEvidence(
  directory: string,
  sourceSha: string,
  captures: readonly TeamCapture[],
) {
  if (!/^[a-f0-9]{40}$/.test(sourceSha))
    throw new Error("TEAM_COLORS_SOURCE_SHA must identify the source commit (40 hex digits)");
  const rows: string[] = [];
  for (const capture of captures) {
    const bytes = await readFile(join(directory, capture.filename));
    const digest = createHash("sha256").update(bytes).digest("hex");
    rows.push(
      `| [${capture.filename}](${capture.filename}) | ${String(capture.fixtureCut)} | ${String(capture.fixtureCut + 1)} | ${String(capture.width)}×${capture.width === 1280 ? "800" : "1080"} | ${capture.theme} | \`${digest}\` |`,
    );
  }
  const command = `TEAM_COLORS_SOURCE_SHA=${sourceSha} TEAM_COLORS_SHOTS=1 pnpm -C frontend exec vitest related --run --maxWorkers 2 src/app/teamColors.browser.test.ts`;
  const manifest = [
    "# Capture provenance",
    "",
    "Handwritten events over Boltons d31d821. Real UI rendered by Vite and Chromium; no provider calls.",
    "",
    `Source SHA: \`${sourceSha}\`. Capture from a clean checkout of this commit.`,
    "Graph: `.runtime/graphs/boltons-d31d821.snapshot.json`, read through `exactFixtures.testSupport.ts`.",
    "Layout: `fixtures/layouts/boltons-d31d821.json`.",
    "Fixture: `fixtures/events/handwritten-team-colors.jsonl`.",
    "The production reducer inserts graph.snapshot at seq 2 after run.started; later fixture sequences shift by one.",
    "The table gives the inclusive fixture and folded event cuts, viewport, theme and PNG SHA-256 for each capture.",
    "Chromium: software WebGL, reduced motion, device scale 1. Themes use data-theme on the document root.",
    "Graph scenes use Fit; chat-flat uses the Chat switch. All images retain the handwritten provenance caption.",
    "Q4 skips pending B and reuses idle C's violet for G; both teams retain their letters.",
    "",
    "Run from the repository root:",
    "```sh",
    command,
    "```",
    "",
    "The suite writes this file beside the PNGs and copies both into `reviewer-copy/`; deliver that entire directory.",
    "",
    "| Capture | Fixture cut | Folded cut | Viewport | Theme | PNG SHA-256 |",
    "| --- | ---: | ---: | --- | --- | --- |",
    ...rows,
    "",
  ].join("\n");
  await writeFile(join(directory, "PROVENANCE.md"), manifest);
  const reviewerCopy = join(directory, "reviewer-copy");
  await rm(reviewerCopy, { recursive: true, force: true });
  await mkdir(reviewerCopy);
  for (const filename of ["PROVENANCE.md", ...captures.map((capture) => capture.filename)])
    await copyFile(join(directory, filename), join(reviewerCopy, filename));
}
