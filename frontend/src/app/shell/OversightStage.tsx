import type { ComponentProps } from "react";
import { prepareDotField } from "../../graph";

import { GraphStage } from "./GraphStage";
import { PracticeGraphStage } from "../challenge/PracticeGraphStage";

// Remains inside the lazy stage: Chat never requests the optional field controller.
export const fieldReady = prepareDotField();

/** Both graph owners stay behind one boundary; never prefetch this module. */
export function OversightStage({
  practice,
  stacked,
  ...props
}: ComponentProps<typeof GraphStage> & {
  practice: boolean;
  stacked: boolean;
}) {
  return practice ? <PracticeGraphStage {...props} stacked={stacked} /> : <GraphStage {...props} />;
}
