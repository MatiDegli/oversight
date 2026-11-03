import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { boltonsPin, railEvents, railScenes, railSource, type RailScene } from "./teamRail.fixture";
// eslint-disable-next-line no-restricted-imports -- Test-only original offline snapshot loader, shared with landed captures.
import { readBoltonsSnapshot } from "../graph/exactFixtures.testSupport";

export const evidenceRoot = new URL("../../../.shots/team-rail/", import.meta.url);
const repo = new URL("../../../", import.meta.url);
export const sha256 = (source: string | Buffer) =>
  createHash("sha256").update(source).digest("hex");
export const expectedFrames = railScenes.flatMap((scene) =>
  (scene === "agents-folded-overflow" ? [1280] : [1920, 1280, 390]).flatMap((width) =>
    ["dark", "day"].map((theme) => `${scene}-${String(width)}-${theme}`),
  ),
);
export type Measure = {
  clientWidth: number;
  scrollWidth: number;
  x: number;
  right: number;
  width: number;
  height: number;
};
export interface RailManifest {
  name: string;
  provenance: string;
  sourceSha: string;
  eventSource: string;
  eventSourceHash: string;
  eventHash: string;
  fixtureCut: number;
  foldedCut: number;
  graph: { pin: string; snapshotHash: string; layout: string; layoutHash: string };
  viewport: { width: number; height: number };
  theme: string;
  setupActions: string[];
  pngHash: string;
  pageErrors: string[];
  measurements: Record<string, Measure[]>;
  lamps: unknown;
  comparison?: { reference: string; referenceHash: string; pngHash: string };
}
export function validateManifest(manifest: RailManifest) {
  if (!expectedFrames.includes(manifest.name) || manifest.provenance !== "handwritten")
    throw new Error("Invalid frame provenance");
  if (!/^[a-f0-9]{40}$/.test(manifest.sourceSha)) throw new Error("Full source SHA required");
  for (const hash of [
    manifest.eventHash,
    manifest.eventSourceHash,
    manifest.pngHash,
    manifest.graph.snapshotHash,
    manifest.graph.layoutHash,
  ])
    if (!/^[a-f0-9]{64}$/.test(hash)) throw new Error("Missing evidence hash");
  if (
    manifest.graph.pin !== boltonsPin ||
    !manifest.eventSource ||
    !manifest.graph.layout ||
    !manifest.setupActions.length ||
    manifest.foldedCut !== manifest.fixtureCut + 1 ||
    manifest.fixtureCut < 1 ||
    !["dark", "day"].includes(manifest.theme)
  )
    throw new Error("Incomplete evidence identity");
  if (manifest.pageErrors.length) throw new Error(`Page errors: ${manifest.pageErrors.join("; ")}`);
  const presence = manifest.name.startsWith("agents-folded") ? "tiles" : "chips";
  for (const key of ["document", "rail", presence, "thread"]) {
    const values = manifest.measurements[key];
    if (!values?.length) throw new Error(`Missing ${key} measurements`);
    for (const value of values)
      if (
        !Object.values(value).every(Number.isFinite) ||
        value.scrollWidth > value.clientWidth + 1 ||
        value.x < -1 ||
        value.right > manifest.viewport.width + 1
      )
        throw new Error(`Unexpected horizontal overflow: ${key} ${JSON.stringify(value)}`);
  }
  if (manifest.name.startsWith("agents-folded") && !manifest.lamps)
    throw new Error("Missing lamp containment");
}
export async function captureContext() {
  const sourceSha = process.env.TEAM_RAIL_SOURCE_SHA ?? "";
  if (!/^[a-f0-9]{40}$/.test(sourceSha)) throw new Error("TEAM_RAIL_SOURCE_SHA must be a full SHA");
  const run = promisify(execFile);
  const options = { cwd: fileURLToPath(repo), timeout: 20_000 };
  const result = await run(
    "python3",
    [
      "-c",
      `
import asyncio, json
async def git(*args):
    proc = await asyncio.create_subprocess_exec('git', *args, stdout=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(proc.communicate(), 20)
    if proc.returncode: raise RuntimeError('git failed')
    return out.decode().strip()
async def main():
    head, dirty = await asyncio.gather(git('rev-parse', 'HEAD'), git('status', '--porcelain'))
    print(json.dumps({'head': head, 'dirty': dirty}))
asyncio.run(main())
`,
    ],
    options,
  );
  const { head, dirty } = JSON.parse(result.stdout) as { head: string; dirty: string };
  if (head !== sourceSha || dirty)
    throw new Error("Capture requires the identified clean source commit");
  const graph = readBoltonsSnapshot();
  if (!graph) throw new Error("Missing original offline Boltons snapshot; no substitute permitted");
  const snapshot = await readFile(new URL(".runtime/graphs/boltons-d31d821.snapshot.json", repo));
  const layout = "fixtures/layouts/boltons-d31d821.json";
  const source = await readFile(new URL("frontend/src/app/teamRail.fixture.ts", repo));
  return {
    sourceSha,
    graph,
    eventSource: "frontend/src/app/teamRail.fixture.ts",
    eventSourceHash: sha256(source),
    graphIdentity: {
      pin: boltonsPin,
      snapshotHash: sha256(snapshot),
      layout,
      layoutHash: sha256(await readFile(new URL(layout, repo))),
    },
  };
}
export async function measureFrame(page: Page, folded = false) {
  return page.evaluate(
    ({ folded }) => {
      const measure = (el: Element) => {
        const box = el.getBoundingClientRect();
        return {
          clientWidth: el.clientWidth,
          scrollWidth: el.scrollWidth,
          x: box.x,
          right: box.right,
          width: box.width,
          height: box.height,
        };
      };
      const all = (selector: string) =>
        Array.from(document.querySelectorAll(selector))
          .filter((el) => el.getBoundingClientRect().width > 0)
          .map(measure);
      return {
        document: [measure(document.documentElement)],
        rail: all(folded ? ".agents-rail" : ".chat-agents"),
        chips: all(".work-chip, .work-file"),
        tiles: folded ? all(".agents-rail-tile") : [],
        thread: all(".conversation-panel"),
      };
    },
    { folded },
  );
}
async function comparison(page: Page, png: Buffer, name: string) {
  const reference = "docs/sketches/v4/conversation.png";
  const referencePng = await readFile(new URL(reference, repo));
  const data = await page.evaluate(
    async (sources) => {
      const images = await Promise.all(
        sources.map(async (source) => {
          const image = new Image();
          image.src = source;
          await image.decode();
          return image;
        }),
      );
      const [product, reference] = images;
      if (!product || !reference) throw new Error("Missing comparison source");
      const canvas = document.createElement("canvas");
      const height = Math.max(product.height, reference.height);
      canvas.width = product.width + reference.width;
      canvas.height = height;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Missing canvas context");
      context.drawImage(product, 0, 0);
      context.drawImage(reference, product.width, 0);
      return canvas.toDataURL("image/png").split(",")[1];
    },
    [png, referencePng].map((buffer) => `data:image/png;base64,${buffer.toString("base64")}`),
  );
  if (!data) throw new Error("Missing comparison PNG");
  const combined = Buffer.from(data, "base64");
  await mkdir(new URL("comparisons/", evidenceRoot), { recursive: true });
  await writeFile(new URL(`comparisons/${name}.png`, evidenceRoot), combined);
  return { reference, referenceHash: sha256(referencePng), pngHash: sha256(combined) };
}
export async function writeFrame(
  page: Page,
  scene: RailScene,
  theme: string,
  actions: string[],
  errors: string[],
  context: Awaited<ReturnType<typeof captureContext>>,
  lamps: unknown = null,
) {
  const viewport = page.viewportSize();
  if (!viewport) throw new Error("Missing viewport");
  const name = `${scene}-${String(viewport.width)}-${theme}`;
  const png = await page.screenshot({ animations: "disabled" });
  const manifest: RailManifest = {
    name,
    provenance: "handwritten",
    sourceSha: context.sourceSha,
    eventSource: context.eventSource,
    eventSourceHash: context.eventSourceHash,
    eventHash: sha256(JSON.stringify(railEvents(scene, context.graph))),
    fixtureCut: railSource(scene).length,
    foldedCut: railEvents(scene, context.graph).length,
    graph: context.graphIdentity,
    viewport,
    theme,
    setupActions: actions,
    pngHash: sha256(png),
    pageErrors: errors,
    measurements: await measureFrame(page, scene === "agents-folded-overflow"),
    lamps,
  };
  manifest.comparison = await comparison(page, png, name);
  validateManifest(manifest);
  await mkdir(evidenceRoot, { recursive: true });
  await writeFile(new URL(`${name}.png`, evidenceRoot), png);
  await writeFile(new URL(`${name}.json`, evidenceRoot), JSON.stringify(manifest, null, 2));
}
export async function validateCoverage(sourceSha: string) {
  for (const name of expectedFrames) {
    const manifest = JSON.parse(
      await readFile(new URL(`${name}.json`, evidenceRoot), "utf8"),
    ) as RailManifest;
    validateManifest(manifest);
    if (!manifest.comparison) throw new Error(`Missing comparison ${name}`);
    if (
      manifest.comparison.pngHash !==
      sha256(await readFile(new URL(`comparisons/${name}.png`, evidenceRoot)))
    )
      throw new Error(`Stale comparison ${name}`);
    if (
      manifest.sourceSha !== sourceSha ||
      manifest.pngHash !== sha256(await readFile(new URL(`${name}.png`, evidenceRoot)))
    )
      throw new Error(`Stale frame ${name}`);
  }
}
