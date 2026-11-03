// Synthetic replay evidence only; no model calls, commands or recording-file edits.
import type { ChangeFacts, ServerEvent } from "@contracts";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { useStore } from "zustand";
import handwritten from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import recorded from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { stateStore as store } from "../state";
// eslint-disable-next-line no-restricted-imports -- Exercise the production replay controller.
import { createReplayPlayer } from "../transport/replayPlayer";
import type { ReplayPlayer } from "../transport";
import { GraphView } from "../graph";
// eslint-disable-next-line no-restricted-imports -- Reuse the production phone disclosure and gutters.
import { PhoneReview, preparePhoneReview } from "../app/challenge/PhoneReview";
// eslint-disable-next-line no-restricted-imports -- Same phone composition as the production surface.
import "../app/challenge/phone.css";
// eslint-disable-next-line no-restricted-imports -- Same phone disclosure anatomy as production.
import "../app/challenge/phoneRows.css";
import { ReviewBody } from "./ReviewBody";
import { ReviewHeader } from "./ReviewHeader";
import { ReviewFacts } from "./ReviewFacts";
import { CONFIGURATION_ATTRIBUTION_PREFIX as prefix } from "./configurationAttribution";

export const diagnostic = `unchanged configuration in ${"configuration/".repeat(24)}<settings>&.toml\n  captured diagnostic  `;
export const secondDiagnostic = "configuration attribution could not be determined";
const host = document.createElement("div");
document.body.replaceChildren(host);
const root = createRoot(host);
let player: ReplayPlayer | undefined;
let changeId = "change-1";
let label = "Synthetic recording · configuration attribution";
let factsSeq = 21;

function Harness() {
  const state = useStore(store, (s) => s.state);
  const proposal = state.changes[changeId]?.proposal;
  const phone = innerWidth < 1280;
  const graph = <GraphView />;
  return (
    <div className={phone ? "phone-practice" : "flex h-screen flex-col bg-page text-text"}>
      <p className="shrink-0 px-4 py-2 text-[13px] text-muted">{label}</p>
      <div className={phone ? "relative h-[168px] shrink-0" : "flex min-h-0 flex-1"}>
        <div aria-label="Graph stage" className="relative h-full min-w-0 flex-1">
          {graph}
        </div>
        {!phone && proposal && (
          <aside className="review-panel flex w-[608px] min-h-0 min-w-0 flex-col bg-page">
            <ReviewHeader state={state} changeId={changeId} onClose={() => undefined} />
            <ReviewBody state={state} changeId={changeId} diff={null} showCrossReview={false} />
          </aside>
        )}
      </div>
      {phone && (
        <div className="pp-main">
          {proposal && <PhoneReview state={state} changeId={changeId} title={proposal.task} />}
        </div>
      )}
      <div hidden data-baseline>
        {proposal && <ReviewFacts state={state} proposal={proposal} />}
      </div>
    </div>
  );
}

function synthetic(mixed: boolean) {
  const events = handwritten
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent)
    .filter((event) => event.seq <= 20);
  const proposed = events.find((event) => event.type === "change.proposed");
  if (proposed?.type !== "change.proposed") throw new Error("Missing synthetic proposal");
  const proposal = proposed.payload;
  const facts: ChangeFacts = {
    change_id: proposal.change_id,
    revision: proposal.revision,
    base_commit: proposal.base_commit,
    head_commit: proposal.head_commit,
    main_commit: proposal.base_commit,
    candidate_tree: "synthetic-tree",
    declared: { files: null, tests_run: null, tests: null },
    edited_paths: proposal.files.map((file) => file.path),
    test_runs: [],
    integrity: [],
    added_tests: [],
    run_findings: [],
    zone: null,
    reach: null,
    complete: false,
    problems: mixed
      ? [
          "detector unknown: timeout",
          `${prefix}${diagnostic}`,
          "source unavailable",
          `${prefix}${secondDiagnostic}`,
        ]
      : [`${prefix}${diagnostic}`, `${prefix}${secondDiagnostic}`],
    summary: null,
    behavior_diff: null,
    measured_cone: null,
    parameter_extensions: null,
    review_cards: null,
    shape: null,
    shape_complete: null,
  };
  events.push({
    seq: 21,
    ts: "2026-10-27T14:03:28.000Z",
    type: "change.facts_recorded",
    payload: facts,
  });
  return events.map((event) => JSON.stringify(event)).join("\n");
}

export const configurationAttribution = {
  diagnostic,
  secondDiagnostic,
  async mount(mode: "alone" | "mixed" | "recorded") {
    player?.dispose();
    await preparePhoneReview();
    label =
      mode === "recorded"
        ? "Recorded run · Oct 27, 2026"
        : "Synthetic recording · configuration attribution";
    const text = mode === "recorded" ? recorded : synthetic(mode === "mixed");
    player = createReplayPlayer({ store, source: { text } });
    await player.ready;
    player.pause();
    const first = player.getSnapshot().events.find((event) => event.type === "change.proposed");
    if (first?.type !== "change.proposed") throw new Error("Missing proposal");
    changeId = first.payload.change_id;
    factsSeq = mode === "recorded" ? first.seq : 21;
    player.seekSeq(factsSeq);
    flushSync(() => {
      root.render(<Harness />);
    });
  },
  seek(after: boolean) {
    flushSync(() => {
      player?.seekSeq(after ? factsSeq : factsSeq - 1);
    });
  },
  restart() {
    flushSync(() => {
      player?.restart();
      player?.pause();
    });
  },
  recording: () => player?.getSnapshot().events ?? [],
  evidence: () => store.getState().state.changeFacts[changeId] ?? [],
};
window.configurationAttribution = configurationAttribution;
declare global {
  interface Window {
    configurationAttribution: typeof configurationAttribution;
  }
}
