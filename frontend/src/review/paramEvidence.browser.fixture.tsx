// Mounted hand-written evidence; no model, commands or transport.
import { useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { useStore } from "zustand";
// eslint-disable-next-line no-restricted-imports -- Capture the incumbent desktop shell.
import { Frame } from "../app/Frame";
// eslint-disable-next-line no-restricted-imports -- Same shell view as production.
import { initialView, openReview } from "../app/view";
// eslint-disable-next-line no-restricted-imports -- Mount the production phone evidence disclosure.
import { PhoneReview, preparePhoneReview } from "../app/challenge/PhoneReview";
// eslint-disable-next-line no-restricted-imports -- Real chip overlay in a mounted phone graph.
import { NodeChips } from "../graph/NodeChips";
import { GraphView } from "../graph";
import { stateStore } from "../state";
import { agentSlotStyle } from "../theme";
import { approvalLocked, approvalLockedCaption, candidateKey, useAnswers } from "./answers";
import { parameterState, nodeId } from "./NewParameterCalls.fixture";
import { visitParameterEvidence } from "./parameterExtensions";
import type { ParameterState } from "./NewParameterCalls.fixture";
import { Check, LockKeyhole, X } from "lucide-react";
// eslint-disable-next-line no-restricted-imports -- Match the existing phone decision dock.
import "../app/challenge/phoneRows.css";
// eslint-disable-next-line no-restricted-imports -- Phone composition styles are owned by the shell.
import "../app/challenge/phone.css";

const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);
function Harness() {
  const state = useStore(stateStore, (s) => s.state);
  const [view, setView] = useState(initialView);
  const candidate = candidateKey(state, "change-1");
  const answers = useAnswers((s) => s.drafts[candidate]) ?? {};
  const proposal = state.changes["change-1"]?.proposal;
  if (innerWidth < 700)
    return (
      <div className="phone-practice" style={agentSlotStyle("A")}>
        <header className="pp-topbar">
          <strong>Oversight</strong>
          <span>demo-repo</span>
        </header>
        <p style={{ padding: "8px 16px", color: "var(--muted)", fontSize: 13 }}>
          Hand-written evidence fixture
        </p>
        <main aria-label="Graph stage" style={{ position: "relative", height: 168, flexShrink: 0 }}>
          <GraphView
            selectedId={nodeId}
            frameIds={[nodeId]}
            litIds={[nodeId]}
            panelOpen
            frameKey="parameter-phone"
          />
          <NodeChips state={state} litIds={[nodeId]} />
        </main>
        <div
          className="pp-main"
          style={{ overflowY: "auto", overflowAnchor: "none", flex: 1, minHeight: 0 }}
        >
          <PhoneReview state={state} changeId="change-1" slot="A" title={proposal?.task ?? ""} />
        </div>
        <footer className="pp-dock">
          {approvalLocked(state.changeFacts["change-1"]?.at(-1)?.review_cards ?? [], answers) && (
            <p className="pp-lock-caption">{approvalLockedCaption}</p>
          )}
          <button
            className="pp-button pp-choice"
            disabled={approvalLocked(
              state.changeFacts["change-1"]?.at(-1)?.review_cards ?? [],
              answers,
            )}
          >
            {approvalLocked(state.changeFacts["change-1"]?.at(-1)?.review_cards ?? [], answers) ? (
              <LockKeyhole size={18} />
            ) : (
              <Check size={18} />
            )}{" "}
            Approve
          </button>
          <button className="pp-button pp-choice">
            <X size={18} />
            Reject
          </button>
        </footer>
      </div>
    );
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={() => undefined}
      canOpenChange={() => true}
      canReview={() => true}
      onOpenChange={() => undefined}
      onReview={(id) => {
        setView((view) => openReview(state, view, id) ?? view);
      }}
      onClosePanel={() => {
        setView(initialView);
      }}
      onDecide={(_id, action) => {
        paramEvidence.decisions.push(action);
      }}
      notice="Hand-written evidence fixture"
    />
  );
}
export const paramEvidence = {
  decisions: [] as string[],
  visit: visitParameterEvidence,
  revise() {
    const state = structuredClone(stateStore.getState().state);
    const proposal = state.changes["change-1"]?.proposal;
    if (proposal) proposal.revision += 1;
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  extensionOnly(name: ParameterState, parameters?: string[]) {
    const state = structuredClone(stateStore.getState().state);
    const facts = state.changeFacts["change-1"]?.at(-1);
    if (facts)
      facts.parameter_extensions =
        parameterState(name).changeFacts["change-1"]?.at(-1)?.parameter_extensions ?? null;
    if (parameters)
      for (const entry of facts?.parameter_extensions ?? []) entry.parameters = parameters;
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  async mount(name: ParameterState) {
    await preparePhoneReview();
    this.update(name);
    flushSync(() => {
      root.render(<Harness />);
    });
  },
  update(name: ParameterState) {
    flushSync(() => {
      stateStore.setState({ state: parameterState(name) });
    });
  },
};
declare global {
  interface Window {
    paramEvidence: typeof paramEvidence;
  }
}
