import type { UsageView } from "@contracts";
import { CircleMinus } from "lucide-react";
import type { State } from "../../state";
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
import { Facts, Identity, Value } from "./UsageDetails";

type Summary = ReturnType<typeof usageSummary>;

function usageSummary(data: UsageView) {
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
  return { open, agents, lead, knownTime, cost, time, active, parallel, perAccepted };
}

function UsageClosing({ summary }: { summary: Summary }) {
  const { agents, lead, open, knownTime, time, active, parallel } = summary;
  return (
    <p className={`usage-closing ${lead || open ? "is-long" : ""}`}>
      {agents} {agents === 1 ? "agent" : "agents"}
      {lead ? " and a lead" : ""}
      {open
        ? agents === 1 && !lead
          ? " has worked "
          : " have worked "
        : knownTime
          ? ": "
          : " worked "}
      <Value text={knownTime ? time.replace("At least", "at least") : time} />
      {knownTime ? " of agent time over " : " together over "}
      <Value text={knownTime ? active.replace("At least", "at least") : active} /> of project time
      {parallel === "—" ? (
        "."
      ) : (
        <>
          , <span className="usage-number">{parallel}</span> at once.
        </>
      )}
    </p>
  );
}

function UsageFacts({ data, summary }: { data: UsageView; summary: Summary }) {
  const { time, active, parallel, cost, perAccepted } = summary;
  const quote = (problems: string[]) => problems.map((problem) => `“${problem}”`).join(" · ");
  return (
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
          ["Revision rounds", data.revision_rounds === null ? "—" : String(data.revision_rounds)],
        ]}
      >
        {data.cost.priced_calls} of {data.cost.calls} calls priced
        {data.cost.problems.length ? ` · ${quote(data.cost.problems)}` : ""}
      </Facts>
    </div>
  );
}

function GroupingControls({
  data,
  group,
  choose,
}: {
  data: UsageView;
  group: Grouping;
  choose: (group: Grouping) => void;
}) {
  return (
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
  );
}

function UsageTableRow({ row, group, state }: { row: UsageRow; group: Grouping; state: State }) {
  return (
    <tr>
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
  );
}

function UsageTable({
  data,
  tableRows,
  group,
  state,
  summary,
}: {
  data: UsageView;
  tableRows: UsageRow[];
  group: Grouping;
  state: State;
  summary: Summary;
}) {
  const { cost, time, open } = summary;
  return (
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
          <UsageTableRow key={row.id} row={row} group={group} state={state} />
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
  );
}

function GroupingExplanation({
  data,
  group,
  tableRows,
}: {
  data: UsageView;
  group: Grouping;
  tableRows: UsageRow[];
}) {
  return (
    <>
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
    </>
  );
}

function UsageSource({ data }: { data: UsageView }) {
  const date = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${data.cost.retrieved}T00:00:00Z`));
  return (
    <p className="usage-source">
      List prices from{" "}
      <a href={data.cost.source_url}>{data.cost.source_url.replace(/^https?:\/\//, "")}</a>, read{" "}
      {date}. Not a bill, and not the budget meter, which counts cached input at the full rate.
    </p>
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
  const summary = usageSummary(data);
  return (
    <section className="usage-panel" aria-label="Usage details">
      <div className="usage-scroll">
        <UsageClosing summary={summary} />
        <UsageFacts data={data} summary={summary} />
        <section className="usage-groups" aria-label="Usage grouping">
          <GroupingControls data={data} group={group} choose={choose} />
          <UsageTable
            data={data}
            tableRows={tableRows}
            group={group}
            state={state}
            summary={summary}
          />
          <GroupingExplanation data={data} group={group} tableRows={tableRows} />
        </section>
        <UsageSource data={data} />
      </div>
      <footer className="usage-scope">
        {recorded ? "Whole recorded run, up to event " : "Up to event "}
        <span className="usage-number">{data.seq}</span>
      </footer>
    </section>
  );
}
