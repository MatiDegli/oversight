import type { ChangeProposed, Slot } from "@contracts";
import { ChevronDown } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import type { State } from "../../state";
import { ChangeFactsRow, ReviewBody } from "../../review";
import { AgentLine } from "./PhoneGraph";
import { Disclosure } from "./PhoneDiff";

export function diffPreview(proposal: ChangeProposed) {
  if (proposal.files.length === 0)
    return `+${String(proposal.lines_added)} −${String(proposal.lines_removed)}`;
  const rest = proposal.files.length - 1;
  return `${proposal.files[0]?.path ?? ""}${rest > 0 ? ` and ${String(rest)} more file${rest === 1 ? "" : "s"}` : ""} · +${String(proposal.lines_added)} −${String(proposal.lines_removed)}`;
}

/** The same evidence projection for a recorded cut or a hand-written fixture. */
export function PhoneReview({
  state,
  changeId,
  title,
  slot,
  diff,
}: {
  state: State;
  changeId: string;
  title: string;
  slot: Slot;
  diff?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const change = state.changes[changeId];
  const proposal = change?.proposal;
  if (!proposal) return null;
  return (
    <>
      <AgentLine state={state} changeId={changeId} slot={slot} />
      <h1>{title}</h1>
      <ChangeFactsRow state={state} changeId={changeId} shortReach />
      <ReviewBody
        state={state}
        changeId={changeId}
        showCrossReview
        phone
        diff={
          <div className="pp-diff-row">
            <button
              className="pp-evidence-row"
              aria-expanded={open}
              aria-controls="phone-recorded-diff"
              onClick={() => {
                setOpen(!open);
              }}
            >
              <span>{open ? "Hide diff" : "Show diff"}</span>
              <span className="pp-row-preview">{diffPreview(proposal)}</span>
              <ChevronDown aria-hidden="true" size={14} />
            </button>
            {open ? diff : <div id="phone-recorded-diff" hidden />}
          </div>
        }
      />
      <div className="pp-decision-rows">
        <Disclosure label="Recorded task" variant="decision" preview={proposal.task}>
          <p className="pp-task">{proposal.task}</p>
        </Disclosure>
      </div>
    </>
  );
}
