import type { ComponentProps } from "react";
import { GraphStage } from "./GraphStage";
import { PracticeGraphStage } from "../challenge/PracticeGraphStage";

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
