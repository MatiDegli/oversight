import { ArrowRight } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import type { State } from "../state";
import { unstableBehaviorSentence } from "./behavior";
import { selectNodeChips } from "./nodeChip";
import type { NodeChip } from "./nodeChip";
import type { GraphProbeHost } from "./probe";

/** Only changed rounded geometry writes styles, so the field's overlay observer stays quiet. */
function place(element: HTMLElement, x: number, y: number, visible: boolean) {
  const next = `translate(${String(Math.round(x))}px, ${String(Math.round(y))}px)`;
  if (element.style.transform !== next) element.style.transform = next;
  const visibility = visible ? "visible" : "hidden";
  if (element.style.visibility !== visibility) element.style.visibility = visibility;
  element.classList.toggle("pointer-events-auto", visible);
}

function usePlacement(
  root: RefObject<HTMLDivElement | null>,
  open: NodeChip | undefined,
  groups: Map<string, NodeChip[]>,
  close: () => void,
) {
  useEffect(() => {
    const layer = root.current;
    const host = layer
      ?.closest('[aria-label="Graph stage"]')
      ?.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    if (!layer || !host) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const origin = layer.getBoundingClientRect();
      const graph = host.getBoundingClientRect();
      for (const group of layer.querySelectorAll<HTMLElement>("[data-chip-node]")) {
        const box = host.oversightGraph?.renderedBox(group.dataset.chipNode ?? "");
        const visible = !!box && box.w >= group.offsetWidth + 16;
        place(
          group,
          (box?.x1 ?? 0) + graph.left - origin.left + 12,
          (box?.y1 ?? 0) + graph.top - origin.top - group.offsetHeight / 2,
          visible,
        );
        if (!open || open.nodeId !== group.dataset.chipNode) continue;
        const popover = layer.querySelector<HTMLElement>("[data-chip-popover]");
        if (!popover) continue;
        if (!visible) {
          close();
          continue;
        }
        const x = box.x2 + graph.left - origin.left - popover.offsetWidth;
        const below = box.y2 + graph.top - origin.top + 8;
        const y =
          below + popover.offsetHeight <= origin.height - 8
            ? below
            : box.y1 + graph.top - origin.top - popover.offsetHeight - 16;
        place(
          popover,
          Math.max(8, Math.min(x, origin.width - popover.offsetWidth - 8)),
          Math.max(8, Math.min(y, origin.height - popover.offsetHeight - 8)),
          true,
        );
      }
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    host.addEventListener("graphrender", schedule);
    const resize = new ResizeObserver(schedule);
    resize.observe(layer);
    for (const child of layer.children) resize.observe(child);
    schedule();
    return () => {
      host.removeEventListener("graphrender", schedule);
      resize.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [root, open, groups, close]);
}

function Count({ text }: { text: string }) {
  return text.split(/(\d+)/).map((part, index) =>
    /\d/.test(part) ? (
      <span key={index} className="font-mono">
        {part}
      </span>
    ) : (
      part
    ),
  );
}

function Popover({ chip, id }: { chip: NodeChip; id: string }) {
  return (
    <section
      id={id}
      data-chip-popover
      aria-label={`${chip.label} behavior samples`}
      className="frosted graph-text-shield absolute left-0 top-0 max-h-[calc(100%-16px)] w-[440px] max-w-[calc(100%-16px)] overflow-auto rounded-(--radius-card) border border-border p-4 text-(length:--caption-size) text-text"
      style={{ visibility: "hidden" }}
    >
      <header className="mb-1 flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate font-mono font-(--label-weight)" title={chip.label}>
          {chip.label}
        </span>
        <span className="shrink-0 text-muted">
          <Count text={chip.header} />
        </span>
      </header>
      <ol>
        {chip.rows.map((row, index) => (
          <li key={index} className="border-b border-border">
            <div className="flex min-h-[26px] items-center gap-2 font-mono">
              <span className="min-w-0 flex-1 truncate text-muted" title={row.inputs}>
                {row.inputs.replace(/^[\w.]+\((.*)\)$/s, "$1")}
              </span>
              <span className="min-w-0 max-w-[25%] wrap-anywhere whitespace-pre-wrap">
                {row.before}
              </span>
              <ArrowRight aria-hidden="true" size={14} className="shrink-0 text-muted" />
              <span className="min-w-0 max-w-[25%] wrap-anywhere whitespace-pre-wrap">
                {row.after}
              </span>
            </div>
            {row.unstable && <p className="pb-2 text-muted">{unstableBehaviorSentence}</p>}
          </li>
        ))}
      </ol>
      {(chip.footer || chip.limitation) && (
        <footer className="mt-2 text-muted">
          {chip.footer && <p>{chip.footer}</p>}
          {chip.limitation && <p className="wrap-anywhere">{chip.limitation}</p>}
        </footer>
      )}
    </section>
  );
}

export function NodeChips({ state, litIds }: { state: State; litIds: readonly string[] | null }) {
  const root = useRef<HTMLDivElement>(null);
  const id = useId();
  const chips = useMemo(() => selectNodeChips(state, litIds), [state, litIds]);
  const groups = useMemo(() => {
    const grouped = new Map<string, NodeChip[]>();
    for (const chip of chips) grouped.set(chip.nodeId, [...(grouped.get(chip.nodeId) ?? []), chip]);
    return grouped;
  }, [chips]);
  const [openKey, setOpen] = useState<string | null>(null);
  const open = chips.find((chip) => chip.key === openKey);
  if (openKey !== null && !open) setOpen(null);
  const close = useMemo(
    () => () => {
      setOpen(null);
    },
    [],
  );
  usePlacement(root, open, groups, close);
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    };
    const outside = (event: PointerEvent) => {
      const target = event.target;
      if (
        target instanceof Element &&
        root.current?.contains(target) &&
        target.closest("button, [data-chip-popover]")
      )
        return;
      close();
    };
    document.addEventListener("keydown", escape);
    document.addEventListener("pointerdown", outside, true);
    return () => {
      document.removeEventListener("keydown", escape);
      document.removeEventListener("pointerdown", outside, true);
    };
  }, [open, close]);
  return (
    <div ref={root} className="pointer-events-none absolute inset-0 overflow-hidden">
      {[...groups].map(([nodeId, entries]) => (
        <div
          key={nodeId}
          data-chip-node={nodeId}
          className="absolute left-0 top-0 flex gap-1"
          style={{ visibility: "hidden" }}
        >
          {entries.map((chip) => (
            <button
              key={chip.key}
              type="button"
              className={`h-[22px] shrink-0 cursor-pointer whitespace-nowrap rounded-(--radius-control) border border-border bg-ground px-2 text-(length:--caption-size) focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text ${chip.kind === "varied" ? "font-normal text-muted" : "font-(--label-weight) text-text"}`}
              aria-label={`${chip.text}, ${chip.label}`}
              aria-expanded={openKey === chip.key}
              aria-controls={`${id}-${encodeURIComponent(chip.key)}`}
              onClick={(event) => {
                event.currentTarget.focus({ preventScroll: true });
                setOpen((current) => (current === chip.key ? null : chip.key));
              }}
            >
              {chip.letter && <span className="text-text">{chip.letter} </span>}
              <Count text={chip.letter ? chip.text.slice(chip.letter.length + 1) : chip.text} />
            </button>
          ))}
        </div>
      ))}
      {open && <Popover chip={open} id={`${id}-${encodeURIComponent(open.key)}`} />}
    </div>
  );
}
