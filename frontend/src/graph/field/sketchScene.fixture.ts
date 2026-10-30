// The approved sketch's busy scene (docs/sketches/life, life.html?freeze=1#watch), read from the
// sketch itself at 1920 by 1080: the canvas size, the boxes of its four lit rows with their weights,
// the boxes that hide lamps (opaque rows and folded cards, then the masks: frame and class headers,
// chips and overlays), and how many lamps its own pass drew at each of the four levels over every
// other cell, with the working ring off. Frame and class bodies are translucent in the sketch, so
// their cells count. The product's field is run on the same scene and must count the same way.
import type { Box } from "./model";

const box = (x1: number, y1: number, x2: number, y2: number): Box => ({ x1, y1, x2, y2 });

export const sketchBusy = {
  width: 1464,
  height: 1002,
  /** The sketch's own count over the cells no cover touches: levels 0, 1/3, 2/3 and 1. */
  counts: [20977, 905, 307, 164] as const,
  /** Editing rows at weight 1, last-read rows at 0.4 (agent A) and 0.3 (agent B). */
  lights: [
    { source: "agent-a", weight: 1, box: box(587, 445, 869, 491) },
    { source: "agent-a", weight: 0.4, box: box(587, 274, 869, 320) },
    { source: "agent-b", weight: 1, box: box(1059, 546, 1317, 592) },
    { source: "agent-b", weight: 0.3, box: box(1059, 489, 1317, 535) },
  ] as const,
  /** Rows and folded cards: opaque, they hide the lamps under them. */
  covers: [
    box(100, 320, 382, 390),
    box(100, 450, 382, 520),
    box(100, 580, 382, 650),
    box(560, 690, 886, 760),
    box(587, 274, 869, 320),
    box(587, 331, 869, 377),
    box(587, 388, 869, 434),
    box(587, 445, 869, 491),
    box(587, 502, 869, 548),
    box(587, 559, 869, 605),
    box(1047, 274, 1329, 320),
    box(1047, 331, 1329, 377),
    box(1059, 432, 1317, 478),
    box(1059, 489, 1317, 535),
    box(1059, 546, 1317, 592),
    box(1059, 603, 1317, 649),
    box(1059, 660, 1317, 706),
    box(1059, 717, 1317, 763),
  ] as readonly Box[],
  /** The sketch's masks, padded by 6: frame and class headers, chips and overlays. */
  masks: [
    box(468.5, 294.5, 507.5, 325.5),
    box(492.5, 614.5, 531.5, 645.5),
    box(680.5, 172.5, 719.5, 203.5),
    box(578, 215, 874, 281),
    box(1038, 215, 1334, 281),
    box(1056, 383, 1322, 439),
    box(18, 14, 440, 68),
    box(1120, 14, 1446, 61),
    box(18, 937, 652, 988),
    box(1396, 872, 1446, 988),
  ] as readonly Box[],
};
