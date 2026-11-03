import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { Check, Circle, CircleDot, LoaderCircle, Minus, GitMerge, X } from "lucide-react";
import type { Plan } from "@contracts";
import { flowSteps, projectPlanFlow, type FlowMark, type PlanFlowRow, type State } from "../state";
import { presentationFor, presentationStyle } from "../teams/presentation";
import "./planFlow.css";

const icons = {
  passed: Check,
  merged: GitMerge,
  working: LoaderCircle,
  waiting: CircleDot,
  failed: X,
  "not needed": Minus,
  "not known": Circle,
};
function Mark({ mark }: { mark: FlowMark }) {
  const Icon = icons[mark];
  return (
    <span className="flow-pip" data-mark={mark}>
      <Icon size={12} aria-hidden="true" />
    </span>
  );
}

function FlowKey({ rows }: { rows: PlanFlowRow[] }) {
  const marks = new Set(rows.flatMap((row) => row.steps.map((item) => item.mark)));
  return (
    <div className="flow-key">
      <strong>Fixed steps for every subtask</strong>
      <div className="flow-legend">
        {(Object.keys(icons) as FlowMark[])
          .filter((mark) => marks.has(mark))
          .map((mark) => (
            <span key={mark}>
              <Mark mark={mark} />
              {mark}
            </span>
          ))}
      </div>
      <p className="flow-step-key">
        {flowSteps.map((label, i) => (
          <span key={label}>
            {i > 0 && <span aria-hidden="true"> › </span>}
            {label}
          </span>
        ))}
      </p>
    </div>
  );
}

function FlowRow({
  row,
  state,
  onReview,
}: {
  row: PlanFlowRow;
  state: State;
  onReview?: (id: string) => void;
}) {
  const identity = presentationFor(state, row.agentId);
  const change = row.changeId ? state.changes[row.changeId] : undefined;
  const canReview = !!change?.proposal && ["ready_for_review", "conflict"].includes(change.status);
  return (
    <div className="flow-row" data-flow-index={row.index} style={presentationStyle(identity)}>
      <span className="flow-node" data-merged={row.merged} data-assigned={!!identity}>
        <span className="sr-only">Subtask #</span>
        {row.index + 1}
      </span>
      <div className="flow-task">{row.task.task}</div>
      <div className="flow-condition">
        <span>{row.condition}</span>
        {identity && (
          <span className="flow-agent" aria-label={`Agent ${identity.label}`}>
            {identity.label}
          </span>
        )}
        {!identity && <span>No agent assigned</span>}
        {row.progress && <span>{row.progress}</span>}
      </div>
      <div className="flow-status">
        <span
          className="flow-rail"
          role="img"
          aria-label={row.steps
            .map((item, i) => `${flowSteps[i] ?? "Step"}: ${item.detail}`)
            .join("; ")}
        >
          {row.steps.map((item, i) => (
            <Mark key={i} mark={item.mark} />
          ))}
        </span>
        <span className="flow-word" data-word={row.word}>
          {row.word}
        </span>
      </div>
      {(row.note || (canReview && row.changeId && onReview)) && (
        <p className="flow-note">
          {row.note}{" "}
          {canReview && row.changeId && onReview && (
            <button
              className="flow-review"
              onClick={() => {
                onReview(row.changeId ?? "");
              }}
            >
              Review change →
            </button>
          )}
        </p>
      )}
    </div>
  );
}

/** Geometry follows wrapped HTML rows; this observer never scrolls, frames or takes focus. */
function FlowRows({
  rows,
  state,
  onReview,
}: {
  rows: PlanFlowRow[];
  state: State;
  onReview?: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState("");
  const parents = [...new Set(rows.flatMap((row) => row.task.depends_on))];
  const gutter = 16 + Math.min(parents.length, 5) * 10;
  useLayoutEffect(() => {
    const node = host.current;
    if (!node) return;
    const measure = () => {
      const box = node.getBoundingClientRect();
      const positions = new Map(
        [...node.querySelectorAll<HTMLElement>("[data-flow-index]")].map((row) => {
          const circle = row.querySelector(".flow-node")?.getBoundingClientRect();
          return [
            Number(row.dataset.flowIndex),
            circle ? circle.top - box.top + circle.height / 2 : 0,
          ];
        }),
      );
      const parents = [...new Set(rows.flatMap((row) => row.task.depends_on))];
      setPaths(
        rows
          .flatMap((row) =>
            row.task.depends_on.map((parent) => {
              const lane = 6 + (parents.indexOf(parent) % 5) * 10;
              const y1 = positions.get(parent) ?? 0,
                y2 = positions.get(row.index) ?? 0;
              return `M${String(gutter)} ${String(y1)}H${String(lane)}V${String(y2)}H${String(gutter - 2)}m-3 -3 3 3-3 3`;
            }),
          )
          .join(" "),
      );
    };
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    node.querySelectorAll(".flow-row").forEach((row) => {
      observer.observe(row);
    });
    return () => {
      observer.disconnect();
    };
  }, [rows, gutter]);
  const waves = [...new Set(rows.map((row) => row.wave))].sort((a, b) => a - b);
  return (
    <div
      className="flow-rows"
      ref={host}
      style={{ "--flow-gutter": `${String(gutter)}px` } as CSSProperties}
    >
      <svg className="flow-dependencies" aria-hidden="true">
        <path d={paths} />
      </svg>
      {waves.map((wave) => (
        <div className="flow-wave" key={wave}>
          <h4>Wave {wave + 1}</h4>
          {rows
            .filter((row) => row.wave === wave)
            .map((row) => (
              <FlowRow key={row.index} row={row} state={state} onReview={onReview} />
            ))}
        </div>
      ))}
    </div>
  );
}

export function PlanFlow({
  state,
  plan,
  dispatched,
  onReview,
}: {
  state: State;
  plan: Plan;
  dispatched: boolean;
  onReview?: (id: string) => void;
}) {
  const rows = projectPlanFlow(state, plan);
  return (
    <div className={`plan-piece plan-flow${rows.length > 6 ? " flow-dense" : ""}`}>
      <header>
        <strong>Plan</strong>
        <span>Drafted by the lead</span>
        <span className="plan-count">
          {rows.length} {rows.length === 1 ? "subtask" : "subtasks"}
          {dispatched && ` · ${String(rows.filter((row) => row.merged).length)} merged`}
        </span>
      </header>
      <FlowKey rows={rows} />
      <FlowRows rows={rows} state={state} onReview={onReview} />
    </div>
  );
}
