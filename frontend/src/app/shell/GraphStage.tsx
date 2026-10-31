import { useCallback, useMemo, useRef, useState } from "react";
import { GraphView, possibleCallers } from "../../graph";
import type { Framing, GraphApi } from "../../graph";
import { ImpactConeCard, selectConeWords } from "../../review";
import type { State } from "../../state";
import { DetailControls, GraphHeading, Legend, ZoomControls } from "../GraphOverlays";
import { fieldInput } from "../fieldScope";
import { graphScope } from "../graphScope";
import { changeNodeIds, panelOpen } from "../view";
import type { View } from "../view";

export function GraphStage({
  state,
  view,
  onNodeTap,
  layoutKey = "",
}: {
  state: State;
  view: View;
  onNodeTap: (id: string) => void;
  layoutKey?: string;
}) {
  const [detail, setDetail] = useState<"Modules" | "Functions" | "Details">("Modules");
  const [zoom, setZoom] = useState(100);
  const open = panelOpen(view);
  // The cone and the change stay lit until the decision; events never open review themselves.
  const { cone, liveCone, litIds } = useMemo(() => graphScope(state, view), [state, view]);
  // The dot field's light follows the same state and the same view, so it never needs an event.
  const field = useMemo(() => fieldInput(state, view), [state, view]);
  // When the panel opens, the camera centers the nodes of the change it shows, review or live diff.
  const shown = view.review ?? view.diff?.changeId ?? null;
  const words = useMemo(() => (shown ? selectConeWords(state, shown) : null), [state, shown]);
  const shownChange = shown === null ? undefined : state.changes[shown];
  const tab = view.tabs?.find((candidate) => candidate.id === view.activeTab);
  const frameIds = useMemo(
    () =>
      tab
        ? [...tab.frameIds]
        : shownChange
          ? changeNodeIds(shownChange)
          : view.file && view.selected
            ? [view.selected]
            : null,
    [tab, shownChange, view.file, view.selected],
  );
  const scopeKey =
    tab?.frameKey ??
    (view.review === null ? (shown ?? view.file?.path ?? null) : `review:${view.review}`);
  const frameKey = open ? (layoutKey ? `${scopeKey ?? "file"}:${layoutKey}` : scopeKey) : null;
  const [framing, setFraming] = useState<{
    key: string | null;
    outOfView: ReadonlySet<string>;
  }>();
  const [routingOmissions, setRoutingOmissions] = useState(0);
  const coneCardRef = useRef<HTMLDivElement>(null);
  const startAreaRef = useRef<HTMLDivElement>(null);
  const graphApi = useRef<GraphApi | null>(null);
  const onFramed = useCallback(
    (result: Framing | null) => {
      setFraming({ key: frameKey, outOfView: result?.outOfView ?? new Set() });
    },
    [frameKey],
  );
  return (
    <main className="graph-stage relative min-w-0 flex-1 bg-ground" aria-label="Graph stage">
      <GraphView
        onZoom={setZoom}
        selectedId={view.selected}
        onNodeTap={onNodeTap}
        panelOpen={open}
        litIds={litIds}
        frameIds={frameIds}
        cone={cone}
        liveCone={liveCone}
        frameKey={frameKey}
        onFramed={onFramed}
        coneCardRef={coneCardRef}
        onRoutingOmissions={setRoutingOmissions}
        apiRef={graphApi}
        startAreaRef={startAreaRef}
        field={field}
      />
      {/* Overlays sit 24px in from the graph's edges: the heading (in watch) or the cone card (in
              review) top left, the legend bottom left, the zoom controls bottom right. */}
      <div className="pointer-events-none absolute inset-(--spacing-overlay) right-[calc(var(--spacing-overlay)+var(--graph-overlay-occlusion,0px))] flex flex-col gap-(--spacing-md)">
        {/* Stretch the height constraint through to the card; keep the bottom row reserved. */}
        <div className="flex min-h-0 items-stretch justify-between gap-(--spacing-md)">
          <div className="flex min-h-0 flex-col items-start gap-(--spacing-md)">
            {cone ? (
              <div
                ref={coneCardRef}
                className={`pointer-events-auto flex min-h-0 flex-col ${cone.entries.length === 0 ? "w-fit max-w-(--cone-card-width)" : "w-(--cone-card-width)"}`}
              >
                <ImpactConeCard
                  cone={cone}
                  words={words}
                  possible={shownChange ? (possibleCallers(state, shownChange, cone) ?? 0) : 0}
                  routingOmissions={routingOmissions}
                  outOfView={framing?.key === frameKey ? framing.outOfView : undefined}
                  onReveal={(id) => graphApi.current?.reveal(id)}
                  nodes={[...(state.graph?.nodes ?? []), ...(shownChange?.graph.nodes_added ?? [])]}
                />
              </div>
            ) : (
              // The narrower graph beside the editor panel is framed on its own nodes, so a
              // heading there would sit on top of them: it is a watch-mode overlay.
              !open && (
                <div className="pointer-events-auto">
                  <GraphHeading state={state} />
                </div>
              )
            )}
          </div>
          {!open && (
            <div className="pointer-events-auto shrink-0">
              <DetailControls
                level={detail}
                zoom={zoom}
                onChange={(level) => {
                  setDetail(level);
                  graphApi.current?.detail?.(level);
                }}
              />
            </div>
          )}
        </div>
        {/* The start camera uses the space between overlays, measured after fonts load. */}
        <div data-graph-clear-area ref={startAreaRef} className="min-h-0 flex-1" />
        <div className="flex shrink-0 items-end justify-between gap-(--spacing-lg)">
          <div className="pointer-events-auto min-w-0">
            <Legend state={state} />
          </div>
          <div className="pointer-events-auto shrink-0">
            <ZoomControls api={graphApi} />
          </div>
        </div>
      </div>
    </main>
  );
}
