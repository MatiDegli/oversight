import type { ChosenReplaySource, RecordedReplay } from "../../transport";
import { manifest, validateManifest } from "./manifest";
import type { PracticeManifest } from "./manifest";

export function availablePractice(
  replay: boolean,
  source: Pick<ChosenReplaySource, "kind">,
  recording: RecordedReplay,
  candidate: PracticeManifest = manifest,
) {
  if (!replay || source.kind !== "real recorded run") return null;
  try {
    return {
      ...validateManifest(candidate, recording),
      revealThroughSeq: candidate.revealThroughSeq,
    };
  } catch {
    return null;
  }
}
