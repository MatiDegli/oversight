import { useContext } from "react";
import type { LeadViewEvent } from "@contracts";
import { resolvePreviewReference, type State } from "../../state";
import { presentationFor } from "../../teams/presentation";
import { agentSlotStyle } from "../../theme";
import { PreviewActionsContext } from "./PreviewActionsContext";

/** Captions remain speech; every action resolves the displayed candidate again. */
export function PreviewOption({
  state,
  event,
  replay = false,
}: {
  state: State;
  event: LeadViewEvent;
  replay?: boolean;
}) {
  const actions = useContext(PreviewActionsContext);
  const displayed = actions?.state ?? state;
  const request = event.payload;
  const identity = presentationFor(
    displayed,
    request.view.kind === "preview" ? request.view.agent_id : null,
  );
  const resolved = resolvePreviewReference(displayed, request);
  const readOnly = replay
    ? "Preview and review are unavailable in replay."
    : actions?.readOnlyReason;
  const reason =
    readOnly ??
    resolved.reason ??
    (!actions?.onPreview || !actions.onReview
      ? "Live preview actions are unavailable in this session."
      : null);
  const activate = (kind: "preview" | "review", origin: HTMLButtonElement) => {
    if (readOnly || !actions || reason || (kind === "preview" && actions.previewRestriction))
      return;
    const fresh = resolvePreviewReference(actions.readState(), request);
    if (!fresh.candidate) return;
    if (kind === "preview") {
      actions.onPreview?.(request);
      // The person's activation can reveal the full option after the column narrows.
      requestAnimationFrame(() => {
        if (!origin.isConnected || !matchMedia("(min-width: 768px)").matches) return;
        const list = origin.closest<HTMLElement>(".conversation-timeline");
        const option = origin.closest<HTMLElement>(".lead-preview-option");
        if (!list || !option) return;
        const below = option.getBoundingClientRect().bottom - list.getBoundingClientRect().bottom;
        if (below > 0) list.scrollTop += below + 8;
      });
    } else actions.onReview?.(fresh.candidate);
  };
  return (
    <div className="lead-preview-option" aria-label="Suggested app preview">
      <p>
        Try the app from{" "}
        <strong style={agentSlotStyle(identity?.hue)} className="lead-view-agent">
          {identity ? `Agent ${identity.label}` : "the referenced agent"}
        </strong>
        .
      </p>
      {request.caption !== null && (
        <p className="lead-preview-caption" dir="auto">
          {request.caption}
        </p>
      )}
      {resolved.candidate || readOnly ? (
        <div className="lead-preview-actions">
          <button
            type="button"
            className="lead-preview-primary"
            disabled={Boolean(reason || actions?.previewRestriction)}
            aria-describedby={actions?.previewRestriction?.describedBy}
            onClick={(event) => {
              activate("preview", event.currentTarget);
            }}
          >
            Preview app
          </button>
          <button
            type="button"
            className="lead-preview-secondary"
            disabled={Boolean(reason)}
            onClick={(event) => {
              activate("review", event.currentTarget);
            }}
          >
            Review change
          </button>
        </div>
      ) : null}
      {reason && <p className="lead-view-note">{reason}</p>}
    </div>
  );
}
