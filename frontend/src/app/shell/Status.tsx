import { PanelBottom } from "lucide-react";
import type { FrameProps } from "../Frame";
import { selectBudgetLine } from "./statusSelectors";
import { focusRing } from "./shared";

export { Dock as BottomDock } from "./dock/Dock";

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
