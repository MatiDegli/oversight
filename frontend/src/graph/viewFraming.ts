import type { Core } from "cytoscape";
import type { GraphViewProps } from "./GraphView";
import { nodesInViewport, paddedArea } from "./camera";
import type { Box, Framing } from "./camera";
import { visibleId } from "./foldVisibility";

export function reportFraming(
  cy: Core,
  host: HTMLElement,
  props: GraphViewProps,
  framing: Framing | null,
) {
  if (!framing || !props.cone) {
    props.onFramed?.(framing);
    return;
  }
  const graphBox = host.getBoundingClientRect();
  const card = props.coneCardRef?.current?.getBoundingClientRect();
  const clear = props.startAreaRef?.current?.getBoundingClientRect();
  const ids = [...(props.frameIds ?? []), ...props.cone.entries.map((entry) => entry.node_id)];
  const shown = ids.map((id) => visibleId(cy, id));
  const nodes = cy.nodes().filter((node) => shown.includes(node.id()));
  const visible = nodesInViewport(
    nodes.map((node) => ({
      id: node.id(),
      ...node.boundingBox({ includeLabels: true, includeOverlays: false }),
    })),
    framing,
    {
      x1: 0,
      y1: 0,
      x2: cy.width(),
      y2: clear ? Math.min(cy.height(), clear.bottom - graphBox.top) : cy.height(),
    },
    card
      ? {
          x1: card.left - graphBox.left,
          y1: card.top - graphBox.top,
          x2: card.right - graphBox.left,
          y2: card.bottom - graphBox.top,
        }
      : undefined,
  );
  props.onFramed?.({
    ...framing,
    framed: new Set(shown.filter((id) => visible.has(id))),
    outOfView: new Set(ids.filter((id) => !visible.has(visibleId(cy, id)))),
  });
}

/** Snapshot panel geometry only when the person opens it, before the single camera move. */
export function panelGeometry(
  host: HTMLElement,
  props: GraphViewProps,
): { occlusion?: Box; area?: Box } {
  const graph = host.getBoundingClientRect();
  const card = props.coneCardRef?.current?.getBoundingClientRect();
  const clear = props.startAreaRef?.current?.getBoundingClientRect();
  const occlusion = card
    ? {
        x1: card.left - graph.left,
        y1: card.top - graph.top,
        x2: card.right - graph.left,
        y2: card.bottom - graph.top,
      }
    : undefined;
  return {
    ...(occlusion ? { occlusion } : {}),
    ...(clear
      ? {
          area: paddedArea(
            graph,
            {
              x1: 0,
              y1: occlusion ? 0 : clear.top - graph.top,
              x2: clear.right - graph.left,
              y2: clear.bottom - graph.top,
            },
            occlusion?.x2,
          ),
        }
      : {}),
  };
}

export function openingCone(props: GraphViewProps): readonly string[] | undefined {
  const cone = props.cone ?? props.liveCone;
  return cone?.entries.filter((entry) => entry.distance <= cone.max_depth).map((e) => e.node_id);
}
