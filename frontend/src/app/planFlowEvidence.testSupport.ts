import { createHash } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";

export const captureNames = [
  ...["proposed", "running", "gates", "failed", "inbox", "large"].flatMap((scene) =>
    [1280, 390].flatMap((width) =>
      ["dark", "day"].map((theme) => `${scene}-${String(width)}-${theme}`),
    ),
  ),
  "phone-390-dark",
  "phone-390-day",
];
const provenance = "Synthetic scripted event scene, rendered by production components";
interface Measurement {
  scene: string;
  theme: string;
  layout: string;
  provenance: string;
  width: number;
  documentWidth: number;
  selectors: Record<string, { clientWidth: number; scrollWidth: number }[]>;
}

/** Fail delivery if any scene, theme, scroll position or measurement is missing. */
export async function writeCaptureMatrix(root: URL) {
  await rm(new URL("matrix.json", root), { force: true });
  const captures = [];
  for (const name of captureNames) {
    const [scene, width, theme] = name.split("-");
    const files = [];
    for (const suffix of [".png", "-end.png", ".json"]) {
      const filename = `${name}${suffix}`;
      const content = await readFile(new URL(filename, root));
      if (suffix.endsWith("png") && content.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a")
        throw new Error(`Invalid PNG: ${filename}`);
      if (suffix === ".json") {
        const data = JSON.parse(content.toString()) as Measurement;
        if (
          data.scene !== scene ||
          data.width !== Number(width) ||
          data.theme !== theme ||
          data.provenance !== provenance ||
          !Number.isFinite(data.documentWidth) ||
          data.documentWidth > data.width ||
          (scene === "inbox" &&
            (data.layout !== "Oversight" ||
              !data.selectors['.plan-group[aria-label="Running plans"]']?.length)) ||
          !data.selectors[".plan-flow"]?.length ||
          data.selectors[".plan-flow"].some((flow) => flow.scrollWidth > flow.clientWidth)
        )
          throw new Error(`Invalid scene measurements: ${filename}`);
      }
      files.push({
        name: filename,
        bytes: content.length,
        sha256: createHash("sha256").update(content).digest("hex"),
      });
    }
    captures.push({ name, files });
  }
  const matrix = { provenance, captures };
  await writeFile(new URL("matrix.json", root), JSON.stringify(matrix, null, 2));
  return matrix;
}
