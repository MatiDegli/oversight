import type { UsageView } from "@contracts";

export type Grouping = "team" | "agent" | "model" | "role";
export interface Figure {
  value: number | null;
  known: number | null;
}
export interface UsageRow {
  id: string;
  kind: string;
  members: string[];
  models: (string | null)[];
  roles: Map<string | null, number>;
  calls: number | null;
  cost: Figure;
  time: Figure;
  unpriced: boolean;
  open: boolean;
}
export const money = (value: number) =>
  `US$${value === 0 ? "0" : value < 0.01 ? Number(value.toPrecision(3)).toFixed(Math.max(0, 2 - Math.floor(Math.log10(Number(value.toPrecision(3)))))) : value.toFixed(2)}`;
export function duration(ms: number): string {
  const tenths = Math.round(ms / 100);
  if (tenths < 600) return `${(tenths / 10).toFixed(1)} s`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 3600)
    return `${String(Math.floor(seconds / 60))} min ${String(seconds % 60).padStart(2, "0")} s`;
  const minutes = Math.round(ms / 60000);
  return `${String(Math.floor(minutes / 60))} h ${String(minutes % 60).padStart(2, "0")} min`;
}
export function figure({ value, known }: Figure, format: (n: number) => string): string {
  return value !== null ? format(value) : known !== null ? `At least ${format(known)}` : "—";
}
export function totals(view: UsageView) {
  const { cost, hours } = view;
  return {
    cost: { value: cost.coverage === "complete" ? cost.usd : null, known: cost.known_usd },
    perAccepted: {
      value: cost.coverage === "complete" ? cost.usd_per_accepted : null,
      known: cost.known_usd_per_accepted,
    },
    time: {
      value: hours.coverage === "complete" ? hours.agent_ms : null,
      known: hours.known_agent_ms,
    },
    active: {
      value: hours.coverage === "complete" ? hours.active_ms : null,
      known: hours.known_active_ms,
    },
  };
}
const identity = (
  group: Grouping,
  item: { agent_id: string | null; model: string | null; role: string | null },
) => (group === "agent" ? item.agent_id : group === "model" ? item.model : item.role) ?? "unknown";

/** Join each inventory independently: review costs belong to authors, intervals to actors. */
export function rows(view: UsageView, group: Grouping): UsageRow[] {
  if (group === "team")
    return view.teams
      .map((team) => ({
        id: team.team,
        kind: team.kind,
        members: team.agents,
        models: [],
        roles: new Map(),
        calls: team.calls,
        cost: {
          value: view.cost.coverage === "incomplete" ? null : team.usd,
          known: team.known_usd,
        },
        time: {
          value: view.hours.coverage === "incomplete" ? null : team.ms,
          known: team.known_ms,
        },
        unpriced:
          !view.cost.rows.filter((row) => row.team === team.team).some((row) => row.priced) &&
          view.cost.rows.some((row) => row.team === team.team),
        open: view.hours.actors.some((actor) => actor.team === team.team && actor.open),
      }))
      .sort((a, b) => Number(a.unpriced) - Number(b.unpriced));
  const keys = new Set(
    [...view.cost.rows, ...view.hours.actors].map((item) => identity(group, item)),
  );
  return [...keys]
    .map((id): UsageRow => {
      const costs = view.cost.rows.filter((row) => identity(group, row) === id);
      const actors = view.hours.actors.filter((actor) => identity(group, actor) === id);
      const observed = actors.filter((actor) => actor.ms !== null);
      const knownTime = observed.length
        ? observed.reduce((sum, actor) => sum + (actor.ms ?? 0), 0)
        : null;
      // Plan calls have no actor type. Missing coding/lead intervals also stay unknown.
      const absent = costs.some(
        (row) =>
          row.role !== "review" &&
          !actors.some(
            (actor) =>
              actor.agent_id === row.agent_id &&
              actor.model === row.model &&
              (row.role === null || actor.role === row.role),
          ),
      );
      const roles = new Map<string | null, number>();
      for (const row of costs) roles.set(row.role, (roles.get(row.role) ?? 0) + row.calls);
      return {
        id,
        kind:
          group === "agent"
            ? id === "unknown"
              ? "reviews"
              : roles.has("lead") || actors.some((actor) => actor.role === "lead")
                ? "lead"
                : roles.has("plan")
                  ? "plan"
                  : "agent"
            : group,
        members: [],
        models: [...new Set([...costs, ...actors].map((item) => item.model))],
        roles,
        calls: costs.length ? costs.reduce((sum, row) => sum + row.calls, 0) : null,
        cost: {
          value:
            costs.length &&
            costs.every((row) => row.usd !== null) &&
            view.cost.coverage !== "incomplete"
              ? costs.reduce((sum, row) => sum + (row.usd ?? 0), 0)
              : null,
          known: costs.length ? costs.reduce((sum, row) => sum + row.known_usd, 0) : null,
        },
        time: {
          value:
            !absent &&
            actors.length &&
            observed.length === actors.length &&
            view.hours.coverage !== "incomplete" &&
            (group !== "model" || view.hours.coverage === "complete")
              ? knownTime
              : null,
          known: knownTime,
        },
        unpriced: costs.length > 0 && costs.every((row) => !row.priced),
        open: actors.some((actor) => actor.open),
      };
    })
    .sort((a, b) => Number(a.unpriced) - Number(b.unpriced));
}
export function groupings(view: UsageView): Grouping[] {
  return [
    ...(view.teams.some((team) => team.kind !== "agent") ? ["team" as const] : []),
    "agent",
    "model",
    ...(view.cost.rows.some((row) => row.role !== null) ? ["role" as const] : []),
  ];
}
export function selection(view: UsageView, previous?: Grouping): Grouping {
  const eligible = groupings(view);
  return previous && eligible.includes(previous)
    ? previous
    : eligible.includes("team") && view.teams.length >= 2
      ? "team"
      : "agent";
}
export const showGrouping = (view: UsageView) =>
  groupings(view).filter((group) => rows(view, group).length > 1).length >= 2;
