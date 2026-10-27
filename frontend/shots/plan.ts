// The pure parts of the capture: which images to take and what to call them, and the last seq of
// the fixture. No browser here, so node's own test runner covers them.
import type { Scene, Theme } from "./scenes.ts";

/** Keep the log through its last edit, before the first proposal; fixtures stay untouched. */
export function editingLog(jsonl: string): string {
  const lines = jsonl.split("\n").filter((line) => line.trim() !== "");
  const proposal = lines.findIndex(
    (line) => (JSON.parse(line) as { type: string }).type === "change.proposed",
  );
  const before = proposal < 0 ? lines : lines.slice(0, proposal);
  const lastEdit = before.findLastIndex(
    (line) => (JSON.parse(line) as { type: string }).type === "change.updated",
  );
  if (lastEdit < 0) throw new Error("the event log has no edit before its first proposal");
  return before.slice(0, lastEdit + 1).join("\n") + "\n";
}

/** The same SSE wire format as the player, without events beyond the selected scene's cut. */
export function replayEventsAfter(jsonl: string, after: number): string {
  return (
    jsonl
      .split("\n")
      .filter((line) => line.trim() !== "")
      .flatMap((line) => {
        const { seq } = JSON.parse(line) as { seq: number };
        return seq > after ? [`id: ${String(seq)}\ndata: ${line}\n\n`] : [];
      })
      .join("") || ": replay complete\n\n"
  );
}

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
