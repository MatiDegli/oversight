import { applyEvent, createInitialState } from "../../state";
import { parseEvent } from "../../transport";
import log from "./progress.jsonl?raw";

/** Hand-written progress test log, never a recorded run. */
export const progressEvents = () => log.trim().split("\n").map(parseEvent);
export const progressMoments = { working: 26, retry: 27, limits: 29 } as const;
export type ProgressMoment = keyof typeof progressMoments;
export const progressState = (moment: ProgressMoment) =>
  progressEvents()
    .filter((event) => event.seq <= progressMoments[moment])
    .reduce(applyEvent, createInitialState());
