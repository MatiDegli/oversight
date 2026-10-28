import { Files, GitPullRequest, Keyboard, Network, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { focusRing } from "./shared";
import { FilesTree } from "./FilesTree";

type Section = "Files" | "Changes";
export function LeftRail({
  watching,
  sidebar,
  onWatch,
  onSidebar,
  onShortcuts,
}: {
  watching: boolean;
  sidebar: Section | null;
  onWatch: () => void;
  onSidebar: (section: Section) => void;
  onShortcuts: () => void;
}) {
  return (
    <nav
      aria-label="Views"
      className="left-rail flex w-(--left-rail-width) shrink-0 flex-col items-center gap-2 border-r border-divider bg-shell py-2.5"
    >
      <button
        type="button"
        aria-label="Watch"
        aria-current={watching ? "page" : undefined}
        title="Graph (watch)"
        onClick={onWatch}
        className={`rail-button ${focusRing}`}
      >
        <Network aria-hidden="true" className="size-5" />
      </button>
      {(
        [
          ["Files", Files],
          ["Changes", GitPullRequest],
        ] as const
      ).map(([label, Icon]) => (
        <button
          key={label}
          type="button"
          aria-label={label}
          aria-expanded={sidebar === label}
          aria-controls="shell-sidebar"
          title={label}
          onClick={() => {
            onSidebar(label);
          }}
          className={`rail-button ${focusRing}`}
        >
          <Icon aria-hidden="true" className="size-5" />
        </button>
      ))}
      <button
        type="button"
        aria-label="Keyboard shortcuts"
        title="Keyboard shortcuts"
        onClick={onShortcuts}
        className={`rail-button mt-auto ${focusRing}`}
      >
        <Keyboard aria-hidden="true" className="size-5" />
      </button>
    </nav>
  );
}

export function Sidebar({ section, onClose }: { section: Section; onClose: () => void }) {
  return (
    <aside id="shell-sidebar" aria-label={`${section} sidebar`} className="shell-sidebar">
      <header className="flex h-10 items-center border-b border-divider px-3.5">
        <h2 className="text-sm font-bold">{section}</h2>
        <button
          type="button"
          aria-label="Close sidebar"
          onClick={onClose}
          className={`ml-auto p-1 ${focusRing}`}
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </header>
      {section === "Files" ? (
        <FilesTree />
      ) : (
        <p className="p-3.5 text-sm text-muted">
          Changes awaiting review and past decisions will appear here.
        </p>
      )}
    </aside>
  );
}

export function ShortcutsSheet({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      aria-label="Keyboard shortcuts"
      onClose={onClose}
      onKeyDown={(event) => {
        // Consume Escape before window listeners can close the active focus tab.
        // Native dialog cancellation also treats Meta+Escape as Escape.
        if (event.key === "Escape" && !event.nativeEvent.isComposing) {
          event.preventDefault();
          if (!event.metaKey) onClose();
        }
      }}
      className="shortcuts-sheet"
    >
      <header className="flex items-center gap-8">
        <h2 className="font-bold">Keyboard shortcuts</h2>
        <button
          type="button"
          aria-label="Close shortcuts"
          onClick={onClose}
          className={`ml-auto p-2 ${focusRing}`}
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </header>
      <dl className="shortcuts-list">
        {[
          ["Toggle sidebar", "Ctrl B"],
          ["Toggle bottom dock", "Ctrl `"],
          ["Close active tab", "Alt W / Escape"],
        ].map(([label, key]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              <kbd>{key}</kbd>
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 text-sm text-muted">
        Shell shortcuts pause while you type. Approve has no shortcut.
      </p>
    </dialog>
  );
}
