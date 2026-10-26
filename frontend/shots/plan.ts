// The pure parts of the capture: which images to take and what to call them, and the last seq of
// the fixture. No browser here, so node's own test runner covers them.
import type { Scene, Theme } from "./scenes.ts";

export interface Job {
  scene: string;
  theme: string;
  edgeShape?: "bezier" | "taxi";
  /** The PNG's name inside .shots/: `<scene>-<theme>[-<edge-shape>].png`. */
  file: string;
  /** Why the job is skipped, or null when it runs. */
  skip: string | null;
}

export function planJobs(scenes: readonly Scene[], themes: readonly Theme[]): Job[] {
  return scenes.flatMap((scene) =>
    themes.flatMap((theme) =>
      (scene.edgeShapes ?? [undefined]).map((edgeShape) => {
        let skip: string | null = null;
        if (!scene.enabled) skip = `scene ${scene.name}: ${scene.needs ?? "disabled"}`;
        else if (!theme.enabled) skip = `theme ${theme.name}: ${theme.needs ?? "disabled"}`;
        return {
          scene: scene.name,
          theme: theme.name,
          edgeShape,
          file: `${scene.name}-${theme.name}${edgeShape ? `-${edgeShape}` : ""}.png`,
          skip,
        };
      }),
    ),
  );
}

/** The seq of the last event in a JSONL log: what a replay must have applied to be complete. */
export function lastSeq(jsonl: string): number {
  const lines = jsonl.split("\n").filter((line) => line.trim() !== "");
  const last = lines.at(-1);
  if (last === undefined) throw new Error("the event log is empty");
  const seq: unknown = (JSON.parse(last) as { seq?: unknown }).seq;
  if (typeof seq !== "number" || !Number.isInteger(seq) || seq < 1) {
    throw new Error(`the last event has no valid seq: ${last.slice(0, 80)}`);
  }
  return seq;
}
