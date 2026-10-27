import { useEffect, useState } from "react";

/** Includes Monaco's hidden input and native edit context, not just ordinary fields. */
export function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    target.closest("input, textarea, select, [contenteditable], .monaco-editor") !== null
  );
}

export function useShell(open: boolean, onClose: () => void) {
  const [layoutRevision, setLayoutRevision] = useState(0);
  useEffect(() => {
    const resized = () => {
      setLayoutRevision((value) => value + 1);
    };
    window.addEventListener("resize", resized);
    return () => {
      window.removeEventListener("resize", resized);
    };
  }, []);
  const [sidebar, setSidebar] = useState<"Files" | "Changes" | null>(null);
  const [dock, setDock] = useState(false);
  const [shortcuts, setShortcuts] = useState(false);
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.metaKey || isTyping(event.target))
        return;
      if (event.key === "Escape" && shortcuts) {
        event.preventDefault();
        setShortcuts(false);
      } else if (
        event.ctrlKey &&
        !event.altKey &&
        !event.shiftKey &&
        event.key.toLowerCase() === "b"
      ) {
        event.preventDefault();
        setSidebar((current) => (current ? null : "Files"));
      } else if (event.ctrlKey && !event.altKey && event.code === "Backquote") {
        event.preventDefault();
        setDock((current) => !current);
      } else if (
        event.altKey &&
        !event.ctrlKey &&
        !event.shiftKey &&
        event.key.toLowerCase() === "w" &&
        open
      ) {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, shortcuts]);
  return {
    layoutRevision,
    sidebar,
    dock,
    shortcuts,
    maximized,
    setShortcuts,
    openSidebar: (section: "Files" | "Changes" | null) => {
      setSidebar((current) => (current === section ? null : section));
    },
    toggleDock: () => {
      setDock((current) => !current);
    },
    toggleMaximize: () => {
      setMaximized((current) => !current);
    },
  };
}
