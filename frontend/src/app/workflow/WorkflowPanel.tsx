import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { ArrowLeft, X } from "lucide-react";
import { selectPlanEvidence } from "../../review";
import { projectPlanFlow, type PlanFlowRow, type State } from "../../state";
import { presentationFor, presentationStyle } from "../../teams/presentation";
import { truncateTask } from "./truncateTask";
import { workflowDetail } from "./workflowDetail";
import "./workflowPanel.css";
import "./workflowPhone.css";

export function workflowOrigin(launcher: HTMLButtonElement) {
  const scroll: { element: HTMLElement; top: number; left: number }[] = [];
  for (
    let element: HTMLElement | null = launcher.parentElement;
    element;
    element = element.parentElement
  ) {
    scroll.push({ element, top: element.scrollTop, left: element.scrollLeft });
  }
  return { launcher, scroll };
}

export function restoreWorkflowOrigin(origin: ReturnType<typeof workflowOrigin> | undefined) {
  for (const { element, top, left } of origin?.scroll ?? []) {
    if (element.isConnected) element.scrollTo({ top, left, behavior: "instant" });
  }
  if (origin?.launcher.isConnected) origin.launcher.focus({ preventScroll: true });
}

export function WorkflowPanel({
  state,
  planId,
  onClose,
  onReview,
  chat = false,
}: {
  state: State;
  planId: string;
  onClose: () => void;
  onReview?: (id: string) => void;
  chat?: boolean;
}) {
  const plan = selectPlanEvidence(state).plans.get(planId)?.payload;
  const rows = useMemo(() => (plan ? projectPlanFlow(state, plan) : []), [state, plan]);
  const { selection, selected, setSelection, close } = useStepSelection(rows);
  const phone = useSyncExternalStore(subscribePhone, phoneSnapshot, () => false) && chat;
  const host = useRef<HTMLElement>(null);
  const detailId = useId();
  usePhoneBoundary(phone, host, close);
  const Icon = phone ? ArrowLeft : X;
  return (
    <section
      ref={host}
      className="workflow-panel"
      aria-label="Workflow"
      role={phone ? "dialog" : "region"}
      aria-modal={phone || undefined}
      data-phone={phone}
      onKeyDown={(event) => {
        workflowKeys(event, phone, onClose);
      }}
    >
      <header className="workflow-header">
        <h2>Workflow</h2>
        <button ref={close} type="button" onClick={onClose}>
          <Icon size={16} aria-hidden="true" />
          {phone ? "Back" : "Close"}
        </button>
      </header>
      <div className="workflow-body">
        {rows.length ? (
          <>
            <WorkflowSteps
              rows={rows}
              selection={selection}
              detailId={detailId}
              onSelect={setSelection}
            />
            <section id={detailId} className="workflow-detail" aria-label="Step detail">
              {selected ? (
                <StepDetail row={selected} state={state} onReview={onReview} />
              ) : (
                <p>Select a step to see its evidence.</p>
              )}
            </section>
          </>
        ) : (
          <p>No steps are available at this point in the run.</p>
        )}
      </div>
    </section>
  );
}

function useStepSelection(rows: PlanFlowRow[]) {
  const [selection, setSelection] = useState<number | null>(null);
  const selected = rows.find((row) => row.index === selection);
  const close = useRef<HTMLButtonElement>(null);
  const previousSelection = useRef<number | null>(null);
  // Clear removed local indices before committing the new evidence. The layout effect
  // still observes the removal and returns keyboard focus after that commit.
  if (selection !== null && !selected) setSelection(null);
  useLayoutEffect(() => {
    close.current?.focus({ preventScroll: true });
  }, []);
  useLayoutEffect(() => {
    if (
      previousSelection.current !== null &&
      !rows.some((row) => row.index === previousSelection.current)
    ) {
      close.current?.focus({ preventScroll: true });
    }
    previousSelection.current = selection;
  }, [selection, rows]);
  return { selection, selected, setSelection, close };
}

function subscribePhone(listener: () => void) {
  const query = window.matchMedia("(max-width: 767px)");
  query.addEventListener("change", listener);
  return () => {
    query.removeEventListener("change", listener);
  };
}
const phoneSnapshot = () => window.matchMedia("(max-width: 767px)").matches;

/** The phone's explicit maximize keeps the originating Chat mounted at its original size. */
function usePhoneBoundary(
  phone: boolean,
  host: RefObject<HTMLElement | null>,
  close: RefObject<HTMLButtonElement | null>,
) {
  useLayoutEffect(() => {
    const panel = host.current;
    if (!phone || !panel) return;
    const saved: { node: HTMLElement; inert: boolean }[] = [];
    for (let node = panel; node.parentElement; node = node.parentElement) {
      for (const sibling of node.parentElement.children) {
        if (sibling === node || !(sibling instanceof HTMLElement)) continue;
        saved.push({ node: sibling, inert: sibling.inert });
        sibling.setAttribute("inert", "");
      }
    }
    if (!panel.contains(document.activeElement)) close.current?.focus({ preventScroll: true });
    return () => {
      for (const entry of saved) entry.node.toggleAttribute("inert", entry.inert);
    };
  }, [phone, host, close]);
}

function workflowKeys(event: KeyboardEvent<HTMLElement>, phone: boolean, onClose: () => void) {
  if (event.defaultPrevented) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    onClose();
  } else if (phone && event.key === "Tab") {
    const controls = [
      ...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
    ];
    const first = controls[0],
      last = controls.at(-1);
    if (event.shiftKey && event.target === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && event.target === last) {
      event.preventDefault();
      first?.focus();
    }
  }
}

function WorkflowSteps({
  rows,
  selection,
  detailId,
  onSelect,
}: {
  rows: PlanFlowRow[];
  selection: number | null;
  detailId: string;
  onSelect: (index: number) => void;
}) {
  return (
    <ol className="workflow-steps" aria-label="Workflow steps">
      {rows.map((row) => (
        <li key={row.index}>
          <button
            type="button"
            className="workflow-step"
            aria-pressed={selection === row.index}
            aria-controls={detailId}
            onClick={() => {
              onSelect(row.index);
            }}
          >
            <span className="workflow-number">{row.index + 1}</span>
            <span>{truncateTask(row.task.task)}</span>
            <span className="workflow-word" data-word={row.word}>
              {row.word}
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function StepDetail({
  row,
  state,
  onReview,
}: {
  row: PlanFlowRow;
  state: State;
  onReview?: (id: string) => void;
}) {
  const detail = workflowDetail(row, state, !!onReview);
  const identity = presentationFor(state, row.agentId);
  return (
    <>
      <h3>{detail.task}</h3>
      <p className="workflow-word" data-word={detail.word}>
        {detail.word}
      </p>
      <p>{detail.condition}</p>
      <DetailFacts detail={detail} identity={identity} />
      <h4>Gate progression</h4>
      <ol className="workflow-gates">
        {detail.gates.map((gate, index) => (
          <li key={index}>
            <span>{gate.label}</span>
            <strong data-mark={gate.mark}>{gate.mark}</strong>
            <p>{gate.detail}</p>
          </li>
        ))}
      </ol>
      <ReviewAction id={detail.reviewId} onReview={onReview} />
    </>
  );
}

/** Withdraw navigation immediately while keeping a focused control in the tab sequence. */
function ReviewAction({ id, onReview }: { id: string | null; onReview?: (id: string) => void }) {
  const [focused, setFocused] = useState(false);
  if (!id && !focused) return null;
  return (
    <button
      type="button"
      aria-disabled={!id || undefined}
      onFocus={() => {
        setFocused(true);
      }}
      onBlur={() => {
        setFocused(false);
      }}
      onClick={() => {
        if (id) onReview?.(id);
      }}
    >
      Review change →
    </button>
  );
}

function DetailFacts({
  detail,
  identity,
}: {
  detail: ReturnType<typeof workflowDetail>;
  identity: ReturnType<typeof presentationFor>;
}) {
  const fields = [
    ["Agent", detail.agent],
    ["Wave", detail.wave],
    ["Merge dependencies", detail.dependencies.join(", ") || "None"],
    ["Progress", detail.progress],
    ["Notes", detail.note],
    ["Role", detail.role],
    ["Model", detail.model],
    ["Mode", detail.mode],
  ] as const;
  return (
    <dl className="workflow-facts">
      <div>
        <dt>Team</dt>
        <dd>
          {identity ? (
            <span className="workflow-team" style={presentationStyle(identity)}>
              <span aria-hidden="true" /> Team {identity.letter}
            </span>
          ) : (
            "Not known"
          )}
        </dd>
      </div>
      {fields.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd className={label === "Agent" || label === "Wave" ? "workflow-data" : undefined}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
