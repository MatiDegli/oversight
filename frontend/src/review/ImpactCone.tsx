import type { GraphNode, ImpactCone } from "@contracts";
import { ScanEye } from "lucide-react";

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
}: {
  cone: ImpactCone;
  nodes: readonly GraphNode[];
  outOfView?: ReadonlySet<string>;
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
      className="min-h-0 overflow-auto rounded-card border border-card-border bg-panel/[calc(var(--cone-card-opacity)*100%)] p-(--spacing-lg) text-(length:--data-size)"
    >
      <h2 className="text-(length:--title-size) font-bold">Possibly affected</h2>
      {cone.entries.length === 0 && (
        <p className="mt-(--spacing-sm) text-muted">No functions in this cone within its bounds.</p>
      )}
      {[...groups].map(([distance, entries]) => (
        <section
          key={distance}
          aria-label={`Distance ${String(distance)}`}
          className="mt-(--spacing-lg)"
        >
          <h3 className="mb-(--spacing-xs) text-muted">
            Distance {distance} · {entries.length} {entries.length === 1 ? "function" : "functions"}
          </h3>
          <ul className="flex flex-col gap-(--spacing-xs)">
            {entries.map((entry) => (
              <li key={entry.node_id} className="flex items-center gap-(--spacing-sm)">
                <DistanceBadge distance={distance} />
                <span className="min-w-0 break-words" title={entry.node_id}>
                  {names.get(entry.node_id) ?? entry.node_id}
                  {outOfView?.has(entry.node_id) && (
                    <span className="flex items-center gap-(--spacing-xs) text-muted">
                      <ScanEye aria-hidden="true" className="size-4 shrink-0" />
                      Out of view
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {cone.omitted_by_depth > 0 && (
        <p className="mt-(--spacing-sm) text-muted">+{cone.omitted_by_depth} omitted by depth</p>
      )}
      {cone.omitted_by_limit > 0 && (
        <p className="mt-(--spacing-sm) text-muted">
          +{cone.omitted_by_limit} omitted by the node limit
        </p>
      )}
    </section>
  );
}
