import { useEffect, useState } from "react";
import type { RefObject } from "react";

/** Report failed routes beside the cone's other omissions, or under the watch heading. */
export function RoutingOmissions({
  hostRef,
  inCone = false,
  onCount,
}: {
  hostRef: RefObject<HTMLDivElement | null>;
  inCone?: boolean;
  onCount?: (count: number) => void;
}) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const report = (event: Event) => {
      const count = (event as CustomEvent<number>).detail;
      setCount(count);
      onCount?.(count);
    };
    host.addEventListener("routingomissions", report);
    return () => {
      host.removeEventListener("routingomissions", report);
    };
  }, [hostRef, onCount]);
  if (!count || inCone) return null;
  return (
    <div className="overlay-fill pointer-events-none absolute top-[calc(var(--spacing-overlay)+var(--button-small-height)+var(--spacing-md))] left-(--spacing-overlay) rounded-control px-(--spacing-sm) py-(--spacing-xs)">
      <p role="status" className="mt-(--spacing-sm) text-sm text-muted">
        {routingOmissionText(count)}
      </p>
    </div>
  );
}

export function routingOmissionText(count: number): string {
  return `+${String(count)} call ${count === 1 ? "route" : "routes"} omitted: no clear lane`;
}
