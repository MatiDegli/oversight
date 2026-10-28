import { useEffect } from "react";

import { isTyping } from "./shell/useShell";
import { closesPanel } from "./view";

/**
 * While the left panel is open, Escape closes it unless something inside took the key: Monaco's
 * find box, or the review comment box, which only leaves itself. The listener sits on the window,
 * so a handler in the panel sees the key first and may claim it with preventDefault.
 */
export function useEscapeCloses(open: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (!event.metaKey && !isTyping(event.target) && closesPanel(event)) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);
}
