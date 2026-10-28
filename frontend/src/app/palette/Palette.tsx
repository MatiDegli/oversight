import { File, Search, Terminal, X, Braces } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState } from "react";
import type { PaletteItem } from "./search";
import { groups, searchItems } from "./search";
import "./palette.css";

export function Palette({
  items,
  returnFocus,
  onClose,
  fileNotice,
  onRetry,
}: {
  items: readonly PaletteItem[];
  returnFocus: HTMLElement | null;
  onClose: () => void;
  fileNotice?: string;
  onRetry?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const matches = searchItems(items, query);
  const selected = matches.find((item) => item.id === active) ?? matches[0];
  const optionId = (item: PaletteItem) => `${id}-${item.id}`;
  useLayoutEffect(() => {
    const element = dialog.current;
    element?.showModal();
    input.current?.focus();
    return () => {
      element?.close();
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, [returnFocus]);
  const choose = (item: PaletteItem) => {
    if (item.unavailable) return;
    onClose();
    item.run();
  };
  return (
    <dialog
      ref={dialog}
      aria-label="Find a function, file or command"
      className="palette"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.metaKey || event.altKey) return;
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="palette-field">
        <Search aria-hidden="true" size={18} />
        <input
          ref={input}
          role="combobox"
          aria-label="Find a function, file or command"
          aria-autocomplete="list"
          aria-expanded="true"
          aria-controls={`${id}-results`}
          aria-activedescendant={selected ? optionId(selected) : undefined}
          aria-describedby={`${id}-count`}
          placeholder="Find a function, file or command"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(null);
          }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.ctrlKey || event.metaKey || event.altKey)
              return;
            if (event.key === "Enter") {
              event.preventDefault();
              if (selected) choose(selected);
            }
            if (!["ArrowDown", "ArrowUp"].includes(event.key) || !matches.length) return;
            event.preventDefault();
            const index = selected ? matches.indexOf(selected) : 0;
            const next =
              matches[
                (index + (event.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length
              ];
            if (next) {
              setActive(next.id);
              document.getElementById(optionId(next))?.scrollIntoView({ block: "nearest" });
            }
          }}
        />
        <button type="button" aria-label="Close palette" onClick={onClose}>
          <X aria-hidden="true" size={18} />
        </button>
      </div>
      <div
        id={`${id}-results`}
        role="listbox"
        aria-label="Search results"
        className="palette-results"
      >
        {[...new Set(matches.map((item) => item.group))].map((group) => {
          const index = groups.indexOf(group);
          const rows = matches.filter((item) => item.group === group);
          const Icon = index === 0 ? Braces : index === 1 ? File : Terminal;
          return (
            rows.length > 0 && (
              <div key={group} role="group" aria-labelledby={`${id}-group-${String(index)}`}>
                <h2 id={`${id}-group-${String(index)}`} className="group-label">
                  {group}
                </h2>
                {rows.map((item) => (
                  <div
                    key={item.id}
                    id={optionId(item)}
                    role="option"
                    aria-selected={selected?.id === item.id}
                    aria-disabled={Boolean(item.unavailable)}
                    className="palette-option"
                    onMouseDown={(event) => {
                      event.preventDefault();
                    }}
                    onClick={() => {
                      choose(item);
                    }}
                  >
                    <Icon aria-hidden="true" size={16} />
                    <span className="palette-copy">
                      <span className={index < 2 ? "palette-code" : ""}>{item.name}</span>
                      {item.detail && (
                        <span
                          className={index < 2 ? "palette-detail palette-code" : "palette-detail"}
                        >
                          {item.detail}
                        </span>
                      )}
                    </span>
                    <span className="palette-kind">{item.kind}</span>
                  </div>
                ))}
              </div>
            )
          );
        })}
      </div>
      {matches.length === 0 && (
        <p className="palette-empty">No matches. Try a shorter name or path.</p>
      )}
      {fileNotice && (
        <p className="palette-notice">
          {fileNotice}{" "}
          {onRetry && (
            <button type="button" onClick={onRetry}>
              Retry files
            </button>
          )}
        </p>
      )}
      <footer>
        <span id={`${id}-count`} role="status">
          {matches.length} {matches.length === 1 ? "match" : "matches"}
        </span>
        <span>
          ↑ ↓ to navigate · Enter to open · <kbd>Esc</kbd> to close
        </span>
      </footer>
    </dialog>
  );
}
