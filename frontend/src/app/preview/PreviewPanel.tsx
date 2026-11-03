import { CircleAlert, Clock3, Monitor, X } from "lucide-react";
import { useSyncExternalStore } from "react";
import type { PreviewController, PreviewView } from "./previewPort";
import { previewReasonCopy } from "./previewReasons";
import "./previewPanel.css";

const states = {
  starting: "Starting",
  ready: "Ready",
  stopping: "Stopping",
  stopped: "Stopped",
  failed: "Failed",
} as const;
/** The caller allocates a slot explicitly; this component never navigates or moves focus. */
export function PreviewPanel({ controller }: { controller: PreviewController }) {
  const view = useSyncExternalStore(controller.subscribe, controller.snapshot, controller.snapshot);
  if (!view.visible) return null;
  const status = view.status;
  const label = status
    ? states[status.state]
    : view.notice === "start_failed"
      ? "Unavailable"
      : "Starting";
  return (
    <aside aria-label="Live preview panel" className="preview-panel">
      <PanelHeader controller={controller} view={view} label={label} />
      <div className="preview-panel-body">
        <PanelMessage view={view} label={label} />
        {view.owned && view.frameUrl && (
          <iframe
            title={`Live preview for ${status?.agent_id ?? "the selected candidate"}`}
            src={view.frameUrl}
            sandbox="allow-forms"
            referrerPolicy="no-referrer"
          />
        )}
      </div>
    </aside>
  );
}

function PanelHeader({
  controller,
  view,
  label,
}: {
  controller: PreviewController;
  view: PreviewView;
  label: string;
}) {
  const status = view.status;
  return (
    <header className="preview-panel-header">
      <div className="preview-panel-title">
        <Monitor size={20} aria-hidden="true" />
        <h2>Live preview</h2>
        <button
          type="button"
          onClick={() => {
            void controller.close();
          }}
        >
          <X size={16} aria-hidden="true" />
          Close
        </button>
      </div>
      <div className="preview-panel-identity">
        <span className="preview-panel-state">{label}</span>
        {status && (
          <span>
            <bdi>{status.agent_id}</bdi> · revision {status.revision}
          </span>
        )}
      </div>
      {status && (
        <p className="preview-panel-provenance">
          <bdi>{status.change_id}</bdi> · head <code>{status.head_commit}</code>
        </p>
      )}
    </header>
  );
}

function PanelMessage({ view, label }: { view: PreviewView; label: string }) {
  const status = view.status;
  const observer =
    !view.owned &&
    status !== null &&
    (status.state === "starting" || status.state === "ready" || status.state === "stopping");
  return (
    <div className="preview-panel-message" role="status" aria-live="polite">
      {observer ? (
        <>
          <CircleAlert size={16} aria-hidden="true" />
          <div>
            <h3>Preview in use in another tab</h3>
            <p>
              Close dismisses this panel only. Wait for ownership to expire before opening a preview
              here.
            </p>
          </div>
        </>
      ) : (
        <>
          {view.uncertain ? (
            <CircleAlert size={16} aria-hidden="true" />
          ) : (
            <Clock3 size={16} aria-hidden="true" />
          )}
          <div>
            <h3>
              {view.uncertain
                ? "Closure is unconfirmed"
                : label === "Ready"
                  ? "Preview is ready"
                  : `${label} preview`}
            </h3>
            {status?.reason && <p>{previewReasonCopy[status.reason]}</p>}
            {!status?.reason && !view.uncertain && (
              <p>
                {label === "Ready"
                  ? "This preview belongs to this tab."
                  : "Waiting for the app to become ready."}
              </p>
            )}
            {view.notice === "renewal_failed" && (
              <p>
                Ownership could not be renewed. Close to request verified closure; replacement is
                unavailable.
              </p>
            )}
            {view.notice === "start_failed" && (
              <p>
                The preview could not be confirmed. Replacement is unavailable until closure is
                verified.
              </p>
            )}
            {view.uncertain && view.frameUrl && (
              <p>
                The current frame stays visible until closure is verified. Another preview cannot
                start yet.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
