import type { ServerEvent } from "@contracts";
import { applyEvent, createInitialState } from "../../state";
import type { ConversationMessage } from "./selectConversation";
import log from "./sketch.jsonl?raw";

/** Hand-written sketch evidence, never presented as a real recorded run. */
export const sketchEvents = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
// On main there is no message event. The caller supplies this at the status boundary.
export const sketchMessages: ConversationMessage[] = [
  { seq: 14, ts: "2026-10-27T14:02:02-03:00", text: "Point to _COUPONS." },
];
export const sketchState = sketchEvents.reduce(applyEvent, createInitialState());
