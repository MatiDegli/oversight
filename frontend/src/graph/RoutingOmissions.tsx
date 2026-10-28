import { useEffect, useState } from "react";
import type { RefObject } from "react";
import { createPortal } from "react-dom";

/** Report failed routes beside the cone's other omissions, or under the watch heading. */
export function RoutingOmissions({
  hostRef,
  coneCardRef,
}: {
  hostRef: RefObject<HTMLDivElement | null>;
  coneCardRef?: RefObject<HTMLDivElement | null>;
}) {
  const [notice, setNotice] = useState<{ count: number; target: Element | null }>({
    count: 0,
    target: null,
  });
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const report = (event: Event) => {
      const count = (event as CustomEvent<number>).detail;
      const target = coneCardRef?.current?.firstElementChild ?? null;
      setNotice((previous) =>
        previous.count === count && previous.target === target ? previous : { count, target },
      );
    };
    host.addEventListener("routingomissions", report);
    return () => {
      host.removeEventListener("routingomissions", report);
    };
  }, [hostRef, coneCardRef]);
  if (!notice.count) return null;
  const message = (
    <p role="status" className="mt-(--spacing-sm) text-sm text-muted">
      {routingOmissionText(notice.count)}
    </p>
  );
  return notice.target ? (
    createPortal(message, notice.target)
  ) : (
    <div className="overlay-fill pointer-events-none absolute top-[calc(var(--spacing-overlay)+var(--button-small-height)+var(--spacing-md))] left-(--spacing-overlay) rounded-control px-(--spacing-sm) py-(--spacing-xs)">
      {message}
    </div>
  );
}

export function routingOmissionText(count: number): string {
  return `+${String(count)} call ${count === 1 ? "route" : "routes"} omitted: no clear lane`;
}
