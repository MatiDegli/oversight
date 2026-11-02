import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
const shots = fileURLToPath(new URL("../../../.shots/chat-leads/", import.meta.url));
/** Reference rectangles are manually read from the supplied raster (about ±2 px). */
export async function compare(
  page: Page,
  capture: Buffer,
  basename: string,
  scene: string,
  width: number,
  theme: string,
  measured: Record<
    string,
    { x: number; y: number; width: number; height: number; fontSize: string } | null
  >,
) {
  const referenceName = `${scene}-${width === 1920 ? "" : "1280-"}${theme}.png`;
  const reference = await readFile(
    new URL(`../../../.shots/chat-r4-reference/${referenceName}`, import.meta.url),
  );
  const combined = await page.evaluate(
    async (sources) => {
      const images = await Promise.all(
        sources.map(async (src) => {
          const image = new Image();
          image.src = src;
          await image.decode();
          return image;
        }),
      );
      const left = images[0],
        right = images[1];
      if (!left || !right) throw new Error("Missing comparison image");
      if (left.width !== right.width || left.height !== right.height)
        throw new Error("Reference dimensions differ");
      const canvas = document.createElement("canvas");
      canvas.width = left.width + right.width;
      canvas.height = left.height;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Missing comparison canvas");
      context.drawImage(left, 0, 0);
      context.drawImage(right, left.width, 0);
      return canvas.toDataURL("image/png").slice("data:image/png;base64,".length);
    },
    [capture, reference].map((buffer) => `data:image/png;base64,${buffer.toString("base64")}`),
  );
  await mkdir(`${shots}compare`, { recursive: true });
  await writeFile(`${shots}compare/${basename}.png`, Buffer.from(combined, "base64"));
  const large = width === 1920,
    stopped = scene === "lead-stopped";
  const targets: [string, string, number, string][] = [
    [".chat-agents", "width", large ? 280 : 260, "Same shell rails as K2a."],
    [".shell-top-bar", "height", 52, "Same shell header."],
    [".chat-lead-row", "width", large ? 263 : 243, "Same Agents list gutters."],
    [
      ".chat-lead-row",
      "height",
      stopped ? (large ? 136 : 111) : large ? 98 : 82,
      "K2b tightens goals to one line and retains team activity in the row and accessible description; full goal is in the selected header.",
    ],
    [
      ".conversation-header h2",
      "fontSize",
      18,
      "DESIGN.md retains the built 16px title role; raw task text remains unchanged.",
    ],
    [
      ".conversation-composer",
      "height",
      large ? 122 : 124,
      "The dispatch replaces the sketch's ended textarea and lock caption with a noninteractive detail and continuation sentence; K2c owns boxed notices.",
    ],
  ];
  const pairs = targets.map(([selector, key, reference, explanation]) => {
    const entry = measured[selector];
    const build = entry
      ? key === "fontSize"
        ? parseFloat(entry.fontSize)
        : entry[key as "width" | "height"]
      : null;
    const delta = build === null ? null : build - reference;
    return {
      selector,
      key,
      reference,
      build,
      delta,
      explanation: delta !== null && Math.abs(delta) > 4 ? explanation : null,
    };
  });
  const path = `${shots}measurements-delta.json`;
  let table: Record<string, unknown> = {};
  try {
    table = JSON.parse(await readFile(path, "utf8")) as Record<string, unknown>;
  } catch {
    /* First capture creates the table. */
  }
  table[basename] = {
    reference: referenceName,
    referenceMethod:
      "Manual raster rectangles, ±2px; build uses getBoundingClientRect/computed font size.",
    pairs,
  };
  await writeFile(path, JSON.stringify(table, null, 2));
}
