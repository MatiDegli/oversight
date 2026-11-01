import type { GraphNode, ImpactCone } from "@contracts";
import type { ConeWord } from "./coneWords";
import { routingOmissionText } from "../graph/RoutingOmissions";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

export function possibleCallersText(count: number): string {
  return `${String(count)} possible ${count === 1 ? "caller" : "callers"}, not followed`;
}

export function impactSummary(cone: ImpactCone, possible = 0): string {
  const count = cone.entries.length;
  if (count === 0 && possible > 0) return `No exact callers · ${possibleCallersText(possible)}`;
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
  routingOmissions = 0,
  possible = 0,
  words,
}: {
  cone: ImpactCone;
  nodes: readonly GraphNode[];
  outOfView?: ReadonlySet<string>;
  /** A click on a row that says "Out of view" asks the camera to pan there; nothing else does. */
  onReveal?: (nodeId: string) => void;
  routingOmissions?: number;
  possible?: number;
  words?: ReadonlyMap<string, ConeWord | null> | null;
}) {
  const hasEntries = cone.entries.length > 0;
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
      className="overlay-fill flex min-h-0 flex-col overflow-hidden rounded-card border border-card-border px-4 py-3.5 text-[13px] leading-[18px]"
    >
      <h2 className="shrink-0 text-[15px] leading-[22px] font-bold">Possibly affected</h2>
      <p className="mt-0.5 shrink-0 text-muted">
        {cone.entries.length
          ? `${String(cone.entries.length)} ${cone.entries.length === 1 ? "function" : "functions"} up to distance ${String(Math.max(...groups.keys()))}`
          : `No exact callers${possible > 0 ? ` · ${possibleCallersText(possible)}` : ""}`}
      </p>
      <div className="min-h-0 overflow-auto">
        {[...groups].map(([distance, entries]) => (
          <section key={distance} aria-label={`Distance ${String(distance)}`} className="mt-3">
            <h3 className="mb-1 flex items-center gap-2 text-muted">
              <DistanceBadge distance={distance} />
              {entries.length} at distance {distance}
            </h3>
            <ul>
              {entries.map((entry) => {
                const name = names.get(entry.node_id) ?? entry.node_id;
                const away = outOfView?.has(entry.node_id) ?? false;
                const word = words?.get(entry.node_id);
                const content = (
                  <>
                    <span className="truncate text-[14px]">{name}</span>
                    <span
                      className={`text-right text-[13px] ${word === "differs" ? "font-semibold text-text" : word === "not measured" ? "text-faint" : "text-muted"}`}
                    >
                      {word}
                    </span>
                    <span className="text-right text-[13px] whitespace-nowrap text-faint">
                      {away && "Out of view"}
                    </span>
                  </>
                );
                return (
                  <li
                    key={entry.node_id}
                    title={entry.node_id}
                    tabIndex={-1}
                    className={`h-7 ${focusRing}`}
                  >
                    {away && onReveal ? (
                      <button
                        type="button"
                        className={`grid h-full w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto_82px] items-center gap-x-2.5 text-left hover:underline ${focusRing}`}
                        onClick={(event) => {
                          // The button becomes plain text once revealed. Keep its focus on the
                          // stable row, without focusing anything on unrelated visibility updates.
                          if (document.activeElement === event.currentTarget)
                            event.currentTarget.parentElement?.focus({ preventScroll: true });
                          onReveal(entry.node_id);
                        }}
                      >
                        {content}
                      </button>
                    ) : (
                      <span className="grid h-full grid-cols-[minmax(0,1fr)_auto_82px] items-center gap-x-2.5">
                        {content}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
      {(routingOmissions > 0 ||
        (hasEntries &&
          (cone.omitted_by_depth > 0 || cone.omitted_by_limit > 0 || possible > 0))) && (
        <footer className="mt-2.5 shrink-0 border-t border-divider pt-2.5 text-muted">
          {hasEntries && possible > 0 && <p>+{possibleCallersText(possible)}</p>}
          {hasEntries && cone.omitted_by_depth > 0 && (
            <p>+{cone.omitted_by_depth} omitted by depth</p>
          )}
          {hasEntries && cone.omitted_by_limit > 0 && (
            <p>+{cone.omitted_by_limit} omitted by the node limit</p>
          )}
          {routingOmissions > 0 && <p role="status">{routingOmissionText(routingOmissions)}</p>}
        </footer>
      )}
    </section>
  );
}
