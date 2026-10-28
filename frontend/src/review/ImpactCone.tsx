import type { GraphNode, ImpactCone } from "@contracts";
import { ScanEye } from "lucide-react";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

export function impactSummary(cone: ImpactCone): string {
  const count = cone.entries.length;
  if (count === 0) return "No functions possibly affected within the cone bounds";
  const distance = Math.max(...cone.entries.map((entry) => entry.distance));
  return `${String(count)} ${count === 1 ? "function" : "functions"} possibly affected, up to distance ${String(distance)}`;
}

export function DistanceBadge({ distance }: { distance: number }) {
  return (
    <span
      aria-label={`Distance ${String(distance)}`}
      className="inline-grid size-(--distance-badge-size) shrink-0 place-items-center rounded-full bg-text text-(length:--distance-badge-font-size) font-(--distance-badge-font-weight) text-page"
    >
      {distance}
    </span>
  );
}

/** The server's bounded cone, in its distance/id order, including entries absent from main. */
export function ImpactConeCard({
  cone,
  nodes,
  outOfView,
  onReveal,
}: {
  cone: ImpactCone;
  nodes: readonly GraphNode[];
  outOfView?: ReadonlySet<string>;
  /** A click on a row that says "Out of view" asks the camera to pan there; nothing else does. */
  onReveal?: (nodeId: string) => void;
}) {
  const names = new Map(nodes.map((node) => [node.id, node.qualname]));
  const groups = new Map<number, ImpactCone["entries"]>();
  for (const entry of cone.entries) {
    const group = groups.get(entry.distance) ?? [];
    group.push(entry);
    groups.set(entry.distance, group);
  }
  return (
    <section
      aria-label="Possibly affected"
      className="overlay-fill flex min-h-0 flex-col overflow-hidden rounded-card border border-card-border p-(--spacing-lg) text-(length:--data-size)"
    >
      <h2 className="shrink-0 text-(length:--title-size) font-bold">Possibly affected</h2>
      <div className="min-h-0 overflow-auto">
        {cone.entries.length === 0 && (
          <p className="mt-(--spacing-sm) text-muted">
            No functions in this cone within its bounds.
          </p>
        )}
        {[...groups].map(([distance, entries]) => (
          <section
            key={distance}
            aria-label={`Distance ${String(distance)}`}
            className="mt-(--spacing-lg)"
          >
            <h3 className="mb-(--spacing-xs) text-muted">
              Distance {distance} · {entries.length}{" "}
              {entries.length === 1 ? "function" : "functions"}
            </h3>
            <ul className="flex flex-col gap-(--spacing-xs)">
              {entries.map((entry) => {
                const name = names.get(entry.node_id) ?? entry.node_id;
                const away = outOfView?.has(entry.node_id) ?? false;
                const note = (
                  <span className="flex items-center gap-(--spacing-xs) text-muted">
                    <ScanEye aria-hidden="true" className="size-4 shrink-0" />
                    Out of view
                  </span>
                );
                return (
                  <li
                    key={entry.node_id}
                    tabIndex={-1}
                    className={`flex items-center gap-(--spacing-sm) ${focusRing}`}
                  >
                    <DistanceBadge distance={distance} />
                    {away && onReveal ? (
                      <button
                        type="button"
                        className={`min-w-0 cursor-pointer break-words text-left hover:underline ${focusRing}`}
                        title={entry.node_id}
                        onClick={(event) => {
                          // The button becomes plain text once revealed. Keep its focus on the
                          // stable row, without focusing anything on unrelated visibility updates.
                          if (document.activeElement === event.currentTarget)
                            event.currentTarget.parentElement?.focus({ preventScroll: true });
                          onReveal(entry.node_id);
                        }}
                      >
                        {name}
                        {note}
                      </button>
                    ) : (
                      <span className="min-w-0 break-words" title={entry.node_id}>
                        {name}
                        {away && note}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
      {(cone.omitted_by_depth > 0 || cone.omitted_by_limit > 0) && (
        <div className="shrink-0">
          {cone.omitted_by_depth > 0 && (
            <p className="mt-(--spacing-sm) text-muted">
              +{cone.omitted_by_depth} omitted by depth
            </p>
          )}
          {cone.omitted_by_limit > 0 && (
            <p className="mt-(--spacing-sm) text-muted">
              +{cone.omitted_by_limit} omitted by the node limit
            </p>
          )}
        </div>
      )}
    </section>
  );
}
