import type { UsageView } from "@contracts";
import { CircleCheck, CircleMinus, CircleX, Eye } from "lucide-react";
import type { ReactNode } from "react";
import type { State } from "../../state";
import { agentLook } from "../../theme";
import type { Grouping, UsageRow } from "./model";

export function Value({ text }: { text: string }) {
  return /^[Aa]t least /.test(text) ? (
    <>
      <span className="usage-qualifier">{text.slice(0, 9)}</span>
      <span className="usage-number">{text.slice(9)}</span>
    </>
  ) : (
    <span
      className={
        text === "—" || text === "No list price" || text === "Not available"
          ? "usage-qualifier"
          : "usage-number"
      }
    >
      {text}
    </span>
  );
}
function Coverage({ coverage }: { coverage: UsageView["cost"]["coverage"] }) {
  const Icon =
    coverage === "complete" ? CircleCheck : coverage === "incomplete" ? CircleX : CircleMinus;
  return (
    <span className={`usage-coverage ${coverage === "incomplete" ? "is-incomplete" : ""}`}>
      <Icon size={14} aria-hidden="true" />
      {coverage === "complete"
        ? "Complete"
        : coverage === "incomplete"
          ? "Incomplete"
          : "Missing metrics"}
    </span>
  );
}
export function Facts({
  name,
  coverage,
  facts,
  children,
}: {
  name: string;
  coverage: UsageView["cost"]["coverage"];
  facts: [string, string, string?][];
  children?: ReactNode;
}) {
  return (
    <section>
      <div className="usage-group-header">
        <h2>{name}</h2>
        <Coverage coverage={coverage} />
      </div>
      <dl>
        {facts.map(([label, value, note]) => (
          <div className="usage-fact" key={label}>
            <dt>
              {label}
              {note && <small>{note}</small>}
            </dt>
            <dd>
              <Value text={value} />
            </dd>
          </div>
        ))}
      </dl>
      {children && <p className="usage-explanation">{children}</p>}
    </section>
  );
}
const modelLabel = (state: State, id: string | null) =>
  id === null
    ? "Unknown model"
    : (state.run?.models.find((profile) => profile.id === id || profile.model === id)?.label ?? id);
function Chip({ id, state, small = false }: { id: string; state: State; small?: boolean }) {
  const slot = state.agents[id]?.slot;
  return (
    <span
      className={`usage-chip ${small ? "is-small" : ""} ${slot ? agentLook[slot].swatch : "bg-raised text-text"}`}
    >
      {slot ?? "?"}
    </span>
  );
}
function TeamIdentity({ row, state }: { row: UsageRow; state: State }) {
  return (
    <>
      <strong>
        {row.kind === "lead"
          ? "Lead team"
          : row.kind === "plan"
            ? "Plan team"
            : `Agent ${state.agents[row.id]?.slot ?? row.id}`}
      </strong>
      <small>
        {row.members.map((id) => (
          <Chip key={id} id={id} state={state} small />
        ))}{" "}
        <span className="usage-number">{row.id}</span>
      </small>
    </>
  );
}
export function Identity({ row, group, state }: { row: UsageRow; group: Grouping; state: State }) {
  if (group === "team") return <TeamIdentity row={row} state={state} />;
  if (group !== "agent")
    return (
      <strong>
        {group === "model"
          ? modelLabel(state, row.id === "unknown" ? null : row.id)
          : row.id === "unknown"
            ? "Unknown role"
            : row.id.slice(0, 1).toUpperCase() + row.id.slice(1)}
      </strong>
    );
  const special = row.kind !== "agent";
  const label =
    row.kind === "reviews"
      ? "Reviews"
      : row.kind === "lead"
        ? "Lead"
        : row.kind === "plan"
          ? "Planner"
          : `Agent ${state.agents[row.id]?.slot ?? row.id}`;
  const order = ["agent", "review", "lead", "plan"];
  const roles = [...row.roles]
    .filter(([role]) => role !== null)
    .sort(([a], [b]) => order.indexOf(a ?? "") - order.indexOf(b ?? ""));
  const detail =
    row.kind === "reviews"
      ? "No agent. Calls and cost are in the author’s row."
      : roles.length > 1
        ? roles
            .map(
              ([role, calls]) =>
                `${String(calls)} as ${role === "review" ? "reviewed author" : (role ?? "unknown role")}`,
            )
            .join(", ")
        : `${special ? `${row.id} · ` : ""}${row.models.map((model) => modelLabel(state, model)).join(", ")}`;
  return (
    <div className="usage-identity">
      {special ? (
        <span className="usage-chip bg-raised text-text">
          {row.kind === "reviews" ? (
            <Eye size={15} aria-hidden="true" />
          ) : row.kind === "lead" ? (
            "L"
          ) : (
            "P"
          )}
        </span>
      ) : (
        <Chip id={row.id} state={state} />
      )}
      <div>
        <strong>{label}</strong>
        <small>
          {detail}
          {row.open ? " · still working" : ""}
        </small>
      </div>
    </div>
  );
}
