import "./usage.css";
import type { UsageView } from "@contracts";
import { CircleAlert, CircleCheck, CircleMinus, CircleX, Eye, Timer } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { State } from "../../state";
import { agentLook } from "../../theme";
import { readUsage } from "./read";
import {
  duration,
  figure,
  groupings,
  money,
  rows,
  selection,
  showGrouping,
  totals,
  type Grouping,
  type UsageRow,
} from "./model";

export default function UsagePanel({ state, recorded }: { state: State; recorded?: boolean }) {
  const runId = state.run?.run_id;
  const [retry, setRetry] = useState(0);
  const [chosen, choose] = useState<Grouping>();
  const [result, setResult] = useState<{ data?: UsageView; error?: string }>();
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    void readUsage(runId, controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ data });
      },
      (error: unknown) => {
        if (!controller.signal.aborted)
          setResult({
            error: error instanceof Error ? error.message : "Usage could not be read. Try again.",
          });
      },
    );
    return () => {
      controller.abort();
    };
  }, [runId, state.lastSeq, retry]);
  if (!runId)
    return (
      <p role="status" className="flex gap-2 p-4 text-muted">
        <Timer aria-hidden="true" size={16} />
        Usage is available when a run starts.
      </p>
    );
  if (result?.error)
    return (
      <div className="p-4">
        <p role="alert" className="flex gap-2 text-muted">
          <CircleAlert aria-hidden="true" size={16} />
          {result.error}
        </p>
        <button
          type="button"
          className="mt-3 underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
          onClick={() => {
            setResult(undefined);
            setRetry((n) => n + 1);
          }}
        >
          Retry usage
        </button>
      </div>
    );
  if (!result?.data || result.data.run_id !== runId)
    return (
      <p role="status" className="flex gap-2 p-4 text-muted">
        <Timer aria-hidden="true" size={16} />
        Loading usage…
      </p>
    );
  return (
    <UsageContent
      data={result.data}
      state={state}
      recorded={recorded}
      chosen={chosen}
      choose={choose}
    />
  );
}

function Value({ text }: { text: string }) {
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
function Facts({
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
function Identity({ row, group, state }: { row: UsageRow; group: Grouping; state: State }) {
  if (group === "team")
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
export function UsageContent({
  data,
  state,
  recorded = false,
  chosen,
  choose,
}: {
  data: UsageView;
  state: State;
  recorded?: boolean;
  chosen?: Grouping;
  choose: (group: Grouping) => void;
}) {
  const group = selection(data, chosen);
  const tableRows = rows(data, group);
  const total = totals(data);
  const open = data.hours.actors.some((actor) => actor.open);
  const agents = new Set(data.teams.flatMap((team) => team.agents)).size;
  const lead = data.teams.some((team) => team.kind === "lead");
  const knownTime = data.hours.coverage !== "complete";
  const cost = figure(total.cost, money);
  const time = figure(total.time, duration);
  const active = figure(total.active, duration);
  const parallel =
    data.hours.coverage === "complete" && data.hours.parallelism !== null
      ? data.hours.parallelism.toFixed(1)
      : "—";
  const perAccepted =
    data.cost.accepted_changes === 0 ? "Not available" : figure(total.perAccepted, money);
  const quote = (problems: string[]) => problems.map((problem) => `“${problem}”`).join(" · ");
  const date = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${data.cost.retrieved}T00:00:00Z`));
  return (
    <section className="usage-panel" aria-label="Usage details">
      <div className="usage-scroll">
        <p className={`usage-closing ${lead || open ? "is-long" : ""}`}>
          {agents} {agents === 1 ? "agent" : "agents"}
          {lead ? " and a lead" : ""}
          {knownTime ? ": " : open ? " have worked " : " worked "}
          <Value text={knownTime ? time.replace("At least", "at least") : time} />
          {knownTime ? " of agent time over " : " together over "}
          <Value text={knownTime ? active.replace("At least", "at least") : active} /> of project
          time
          {parallel === "—" ? (
            "."
          ) : (
            <>
              , <span className="usage-number">{parallel}</span> at once.
            </>
          )}
        </p>
        <div className="usage-facts">
          <Facts
            name="Time"
            coverage={data.hours.coverage}
            facts={[
              ["Agent time", time],
              ["Project time", active],
              ["Working at once", parallel],
            ]}
          >
            {quote(data.hours.problems)}
          </Facts>
          <Facts
            name="Cost"
            coverage={data.cost.coverage}
            facts={[
              ["List-price cost", cost],
              [
                "Per accepted change",
                perAccepted,
                data.cost.accepted_changes === 0 ? "No change accepted yet" : undefined,
              ],
              ["Accepted changes", String(data.cost.accepted_changes)],
              [
                "Revision rounds",
                data.revision_rounds === null ? "—" : String(data.revision_rounds),
              ],
            ]}
          >
            {data.cost.priced_calls} of {data.cost.calls} calls priced
            {data.cost.problems.length ? ` · ${quote(data.cost.problems)}` : ""}
          </Facts>
        </div>
        <section className="usage-groups" aria-label="Usage grouping">
          <div className="usage-grouping">
            {showGrouping(data) ? (
              groupings(data).map((option) => (
                <button
                  type="button"
                  key={option}
                  aria-pressed={group === option}
                  onClick={() => {
                    choose(option);
                  }}
                >
                  By {option}
                </button>
              ))
            ) : (
              <h2>By {group}</h2>
            )}
          </div>
          <table>
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">{group}</span>
                </th>
                <th scope="col">Calls</th>
                <th scope="col">Cost</th>
                <th scope="col">Time</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row) => (
                <tr key={row.id}>
                  <th scope="row">
                    <Identity row={row} group={group} state={state} />
                  </th>
                  <td>
                    <Value text={row.calls === null ? "—" : String(row.calls)} />
                  </td>
                  <td>
                    <Value text={row.unpriced ? "No list price" : figure(row.cost, money)} />
                    {group === "team" && row.cost.value === null && (
                      <small className="usage-team-note">
                        <CircleMinus size={13} aria-hidden="true" />
                        Includes unknown work
                      </small>
                    )}
                  </td>
                  <td>
                    <Value text={figure(row.time, duration)} />
                    {row.open && <span className="usage-qualifier"> so far</span>}
                  </td>
                </tr>
              ))}
            </tbody>
            {!(group === "team" && tableRows.length === 1) && (
              <tfoot>
                <tr>
                  <th scope="row">Total</th>
                  <td className="usage-number">{data.cost.calls}</td>
                  <td>
                    <Value text={cost} />
                  </td>
                  <td>
                    <Value text={time} />
                    {open && <span className="usage-qualifier"> so far</span>}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
          {group === "team" && tableRows.length === 1 ? (
            <p className="usage-explanation">
              One team, so a total row would repeat it.
              {!data.teams_add_up && " Figures include unknown work."}
            </p>
          ) : (
            !data.teams_add_up && (
              <p className="usage-explanation">
                — means unknown. Incomplete metrics have no exact total.
              </p>
            )
          )}
        </section>
        <p className="usage-source">
          List prices from{" "}
          <a href={data.cost.source_url}>{data.cost.source_url.replace(/^https?:\/\//, "")}</a>,
          read {date}. Not a bill, and not the budget meter, which counts cached input at the full
          rate.
        </p>
      </div>
      <footer className="usage-scope">
        {recorded ? "Whole recorded run, up to event " : "Up to event "}
        <span className="usage-number">{data.seq}</span>
      </footer>
    </section>
  );
}
