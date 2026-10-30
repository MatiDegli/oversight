// UI fixtures from committed event logs; never a recorded model run.
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import inbox from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import ordinary from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { ReviewPanel } from "./ReviewPanel";

export const headerCases = {
  a: { log: inbox, end: Infinity, changeId: "change-3" },
  b: { log: ordinary, end: 20, changeId: "change-1" },
  g: { log: inbox, end: Infinity, changeId: "change-1" },
};
export function headerState(name: keyof typeof headerCases) {
  const { log, end } = headerCases[name];
  return log
    .trim()
    .split("\n")
    .map(parseEvent)
    .filter((event) => event.seq <= end)
    .reduce(applyEvent, createInitialState());
}

export function mountHeader(name: keyof typeof headerCases) {
  const host = document.createElement("div");
  host.style.cssText = "display:flex;width:880px;height:940px;margin:92px 0 0 56px";
  document.body.replaceChildren(host);
  flushSync(() => {
    createRoot(host).render(
      <ReviewPanel
        state={headerState(name)}
        changeId={headerCases[name].changeId}
        onClose={() => undefined}
      />,
    );
  });
}
