import { useState } from "react";
import { PanelBottom, X } from "lucide-react";
import type { FrameProps } from "../Frame";
import { selectBudgetLine } from "./statusSelectors";
import { focusRing } from "./shared";

export function BottomDock({ onClose }: { onClose: () => void }) {
  const [active, setActive] = useState("Tests");
  return (
    <section aria-label="Bottom dock" className="bottom-dock">
      <div className="flex h-9 shrink-0 border-b border-divider">
        <div role="tablist" aria-label="Dock">
          {["Tests", "Activity log"].map((label) => (
            <button
              key={label}
              type="button"
              role="tab"
              aria-selected={active === label}
              aria-controls="dock-content"
              onClick={() => {
                setActive(label);
              }}
              className={`dock-tab h-full px-4 text-sm ${focusRing}`}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          aria-label="Close dock"
          onClick={onClose}
          className={`ml-auto px-3 text-muted ${focusRing}`}
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </div>
      <div id="dock-content" role="tabpanel" aria-label={active} className="p-4 text-sm text-muted">
        {active === "Tests"
          ? "Test results will appear here."
          : "The run’s activity log will appear here."}
      </div>
    </section>
  );
}

export function StatusLine(props: FrameProps & { dock: boolean; onToggleDock: () => void }) {
  const budget = selectBudgetLine(props.state);
  return (
    <footer aria-label="Status line" className="status-line">
      <span>{props.replayLabel ?? (props.replayOf ? `Replay · ${props.replayOf}` : "Live")}</span>
      {budget && (
        <span role="status" className="text-muted">
          {budget}
        </span>
      )}
      {props.notice && (
        <div role="status" className="truncate">
          {props.notice}
        </div>
      )}
      {props.connection && (
        <span role="status">
          {props.connection === "lost"
            ? "Connection lost"
            : props.connection === "connecting"
              ? "Connecting"
              : "Connected"}
        </span>
      )}
      {props.connection === "lost" && props.onRetry && (
        <button type="button" onClick={props.onRetry} className={`underline ${focusRing}`}>
          Retry connection
        </button>
      )}
      <button
        type="button"
        aria-label="Toggle bottom dock"
        aria-expanded={props.dock}
        onClick={props.onToggleDock}
        className={`ml-auto flex items-center gap-2 ${focusRing}`}
      >
        <PanelBottom aria-hidden="true" className="size-3.5" />
        Dock <kbd>Ctrl `</kbd>
      </button>
    </footer>
  );
}
