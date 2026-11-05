import { Check, ChevronDown, Cpu } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, Ref } from "react";

const item =
  "flex min-h-9 w-full items-center gap-2 rounded-control px-2 text-left text-(length:--data-size) whitespace-nowrap text-text hover:bg-raised focus:bg-raised focus:outline-none";

interface Choices {
  models: { id: string; label: string }[];
  current: string | undefined;
  onModel: (id: string) => void;
  onSettings?: () => void;
}

/** The open menu: each model, the current one checked, then Settings, where model keys live. */
function ModelMenuItems({
  id,
  menuRef,
  models,
  current,
  onKey,
  onPick,
  onSettings,
}: Omit<Choices, "onModel"> & {
  id: string;
  menuRef: Ref<HTMLDivElement>;
  onKey: (event: KeyboardEvent) => void;
  onPick: (id: string) => void;
}) {
  return (
    <div
      ref={menuRef}
      id={id}
      role="menu"
      aria-label="Model for the next agent"
      onKeyDown={onKey}
      className="absolute top-full left-0 z-20 mt-1 flex min-w-56 flex-col gap-0.5 rounded-control border border-control-edge bg-page-raised p-1 shadow-(--card-shadow)"
    >
      {models.map((model) => (
        <button
          key={model.id}
          type="button"
          role="menuitemradio"
          aria-checked={model.id === current}
          tabIndex={-1}
          onClick={() => {
            onPick(model.id);
          }}
          className={item}
        >
          <Check
            aria-hidden="true"
            className={`size-4 shrink-0 ${model.id === current ? "text-text" : "invisible"}`}
          />
          {model.label}
        </button>
      ))}
      {onSettings && (
        <button
          type="button"
          role="menuitem"
          tabIndex={-1}
          onClick={onSettings}
          className={`${item} border-t border-page-border text-muted`}
        >
          <span aria-hidden="true" className="size-4 shrink-0" />
          Models and keys in Settings
        </button>
      )}
    </div>
  );
}

/** Arrows, Home and End move between the items; Escape and Tab close the menu onto its chip. */
function menuKey(event: KeyboardEvent, all: HTMLElement[], close: () => void) {
  const at = all.indexOf(document.activeElement as HTMLElement);
  const move: Record<string, number> = {
    ArrowDown: (at + 1) % all.length,
    ArrowUp: (at - 1 + all.length) % all.length,
    Home: 0,
    End: all.length - 1,
  };
  if (event.key in move) {
    event.preventDefault();
    all[move[event.key] ?? 0]?.focus();
  } else if (event.key === "Escape" || event.key === "Tab") {
    event.preventDefault();
    close();
  }
}

/**
 * The model chip as a menu button. Only the person's opening moves focus into the menu; Escape and
 * a pick return it to the chip, and a click on another control leaves that control its focus.
 */
export function ModelMenu({
  label,
  locked,
  chipClass,
  onModel,
  onSettings,
  ...choices
}: Choices & { label: string; locked: boolean; chipClass: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const chipRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const items = () => [
    ...(menuRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []),
  ];
  const close = () => {
    setOpen(false);
    chipRef.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    (menuRef.current?.querySelector<HTMLElement>('[aria-checked="true"]') ?? items()[0])?.focus();
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || chipRef.current?.contains(target)) return;
      setOpen(false);
      window.setTimeout(() => {
        if (document.activeElement === document.body) chipRef.current?.focus();
      }, 0);
    };
    document.addEventListener("pointerdown", outside);
    return () => {
      document.removeEventListener("pointerdown", outside);
    };
  }, [open]);
  return (
    <div className="relative">
      <button
        ref={chipRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? `${id}-menu` : undefined}
        aria-label={`Model: ${label}`}
        aria-disabled={locked}
        onClick={() => {
          if (!locked) setOpen(!open);
        }}
        className={`${chipClass} hover:bg-raised aria-disabled:cursor-not-allowed`}
      >
        <Cpu aria-hidden="true" className="size-4 shrink-0 text-muted" />
        {label}
        <ChevronDown aria-hidden="true" className="size-3.5 shrink-0 text-muted" />
      </button>
      {open && (
        <ModelMenuItems
          id={`${id}-menu`}
          menuRef={menuRef}
          onKey={(event) => {
            menuKey(event, items(), close);
          }}
          onPick={(model) => {
            onModel(model);
            close();
          }}
          onSettings={
            onSettings &&
            (() => {
              setOpen(false);
              onSettings();
            })
          }
          {...choices}
        />
      )}
    </div>
  );
}
