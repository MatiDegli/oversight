import { Monitor, X, ArrowLeft } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { agentSlotStyle, type PaletteKey } from "../../theme";
import type { PreviewClock, PreviewController, PreviewView } from "./previewPort";
import { previewReasonCopy } from "./previewReasons";
import "./previewPanel.css";

const states = {
  starting: "Starting",
  ready: "Ready",
  stopping: "Stopping",
  stopped: "Stopped",
  failed: "Refused",
} as const;
const startFailedCopy =
  "The preview could not be confirmed. Replacement is unavailable until closure is verified.";
function frameProgress() {
  let loaded: string | null = null;
  const listeners = new Set<() => void>();
  return {
    snapshot: () => loaded,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    load(url: string) {
      loaded = url;
      for (const listener of listeners) listener();
    },
  };
}
const noElapsed = () => 0;
/** The caller allocates a slot explicitly. Arrivals and loading never move focus. */
export function PreviewPanel({
  controller,
  clock,
  elapsed = noElapsed,
  identity,
  replay = false,
}: {
  controller: PreviewController;
  clock?: PreviewClock;
  elapsed?: () => number;
  identity?: (agentId: string | undefined) => { label: string; hue: PaletteKey } | undefined;
  replay?: boolean;
}) {
  const snapshot = useSyncExternalStore(
    controller.subscribe,
    controller.snapshot,
    controller.snapshot,
  );
  const view =
    snapshot.status?.reason === "cleanup_unverified" ? { ...snapshot, uncertain: true } : snapshot;
  const [progress] = useState(frameProgress);
  if (!view.visible) return null;
  const status = view.status;
  const label =
    status?.state === "stopping"
      ? "Stopping"
      : view.uncertain
        ? "Unconfirmed"
        : status
          ? states[status.state]
          : "Starting";
  const name = identity?.(status?.agent_id);
  const frame = !replay && view.owned && status?.state === "ready" && view.frameUrl;
  const close = () => {
    void controller.close();
  };
  return (
    <aside aria-label="Live preview panel" className="preview-panel">
      <header className="preview-panel-header">
        <div className="preview-panel-title">
          <Monitor size={16} aria-hidden="true" />
          <h2>Live preview</h2>
          <strong style={agentSlotStyle(name?.hue)} className="preview-panel-agent">
            {name ? `Agent ${name.label}` : "Selected app"}
          </strong>
          <button
            type="button"
            onClick={close}
            title={view.owned ? "Stop app and close preview" : "Dismiss preview panel"}
          >
            <span className="preview-close-desktop">
              <X size={16} aria-hidden="true" />
              Close
            </span>
            <span className="preview-close-phone">
              <ArrowLeft size={16} aria-hidden="true" />
              Back
            </span>
          </button>
        </div>
        <div className="preview-panel-identity">
          <span className="preview-panel-state">{replay ? "Replay fixture · inert" : label}</span>
          <PreviewPhase
            view={view}
            loaded={progress}
            clock={clock}
            elapsed={elapsed}
            replay={replay}
          />
          {status && (
            <details className="preview-panel-provenance">
              <summary>Revision {status.revision}</summary>
              <p>
                <bdi>{status.change_id}</bdi> · head <code>{status.head_commit}</code>
              </p>
            </details>
          )}
        </div>
      </header>
      <div className="preview-panel-body">
        {replay ? (
          <div className="preview-panel-message" role="status">
            <h3>Replay panel fixture</h3>
            <p>Live previews are unavailable in replay.</p>
          </div>
        ) : (
          (!frame || view.uncertain) && <PanelMessage view={view} label={label} />
        )}
        {frame && (
          <iframe
            title={
              name ? `Live preview for Agent ${name.label}` : "Live preview for the selected app"
            }
            src={frame}
            sandbox="allow-scripts allow-same-origin allow-forms"
            referrerPolicy="no-referrer"
            onLoad={() => {
              progress.load(frame);
            }}
          />
        )}
      </div>
    </aside>
  );
}

/** The elapsed clock subscribes only this header fragment, at most once per second. */
function PreviewPhase({
  view,
  loaded,
  clock,
  elapsed,
  replay,
}: {
  view: PreviewView;
  loaded: ReturnType<typeof frameProgress>;
  clock?: PreviewClock;
  elapsed: () => number;
  replay: boolean;
}) {
  const loadedUrl = useSyncExternalStore(loaded.subscribe, loaded.snapshot, loaded.snapshot);
  const [seconds, setSeconds] = useState(elapsed);
  const waiting =
    !replay &&
    (view.owned || view.notice === "opening" || view.pending) &&
    !view.uncertain &&
    (view.pending ||
      view.status?.state === "starting" ||
      (view.status?.state === "ready" && (!view.frameUrl || loadedUrl !== view.frameUrl)));
  useEffect(() => {
    if (!waiting || !clock) return;
    let off = () => {};
    const tick = () => {
      setSeconds(elapsed());
      off = clock.schedule(tick, 1000);
    };
    off = clock.schedule(tick, 1000);
    return () => {
      off();
    };
  }, [waiting, clock, elapsed]);
  if (!waiting) return null;
  const phase =
    view.status?.state === "ready"
      ? "Loading the page"
      : view.status?.state === "starting"
        ? "App starting"
        : "Request sent";
  return (
    <span className="preview-panel-phase">
      {phase} · {seconds} s
    </span>
  );
}
function PanelMessage({ view, label }: { view: PreviewView; label: string }) {
  const status = view.status;
  const observer =
    !view.owned && status && ["starting", "ready", "stopping"].includes(status.state);
  const heading = observer
    ? "Preview in use in another tab"
    : view.uncertain
      ? "Closure is unconfirmed"
      : status?.state === "failed"
        ? "Preview refused"
        : `${label} preview`;
  return (
    <div
      className="preview-panel-message"
      role={view.uncertain && !view.pending ? "alert" : "status"}
    >
      <h3>{heading}</h3>
      {observer ? (
        <p>
          Close dismisses this panel only. Wait for ownership to expire before opening a preview
          here.
        </p>
      ) : status?.state === "stopping" ||
        (view.pending && view.notice === "closure_unconfirmed") ? (
        <p>Waiting for verified closure.</p>
      ) : status?.reason ? (
        <p>{previewReasonCopy[status.reason]}</p>
      ) : (
        !view.uncertain && (
          <p>Waiting for the app to become ready. You can close this preview at any time.</p>
        )
      )}
      {view.notice === "start_failed" && <p>{startFailedCopy}</p>}
      {view.notice === "renewal_failed" && (
        <p>
          Ownership could not be renewed. Close to request verified closure; replacement is
          unavailable.
        </p>
      )}
      {view.notice === "closure_unconfirmed" && !view.pending && (
        <p>
          Close to request verified closure. Another preview cannot start until closure is verified.
        </p>
      )}
    </div>
  );
}
