import { X } from "lucide-react";
import { LiveDiff } from "../../editor";
import { selectLiveDiff } from "../../state";
import type { State } from "../../state";
import type { View } from "../view";
import { agentLook, focusRing, stateWords } from "./shared";
export function LivePanel({
  state,
  view,
  onClose,
}: {
  state: State;
  view: View;
  onClose: () => void;
}) {
  const target = view.diff;
  const data = target ? selectLiveDiff(state, target.changeId, target.path) : undefined;
  const agent = data ? state.agents[data.agentId] : undefined;
  return (
    <aside
      aria-label="Live diff panel"
      className="flex w-[min(var(--review-panel-width),46vw)] shrink-0 flex-col border-r border-divider bg-page"
    >
      <div className="flex items-center gap-2.5 border-b border-page-border px-4 py-2.5">
        {agent && (
          <>
            <span
              aria-hidden="true"
              className={`size-3 shrink-0 rounded-label ${agentLook[agent.slot].fill}`}
            />
            <span className="shrink-0 font-bold">Agent {agent.slot}</span>
            <span
              className={`shrink-0 rounded-full px-2 py-1 text-sm ${agentLook[agent.slot].tint}`}
            >
              {stateWords[agent.state][0]}
            </span>
            <p title={agent.task} className="min-w-0 truncate text-sm text-muted">
              {agent.task}
            </p>
          </>
        )}
        <button
          type="button"
          aria-label="Close live diff"
          title="Close (Escape)"
          onClick={onClose}
          className={`ml-auto grid size-(--button-small-height) shrink-0 place-items-center rounded-control text-muted hover:bg-raised hover:text-text ${focusRing}`}
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1">
        <LiveDiff data={data} />
      </div>
    </aside>
  );
}
