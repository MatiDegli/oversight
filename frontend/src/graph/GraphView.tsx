import cytoscape from "cytoscape";
import type { Core, NodeSingular } from "cytoscape";
import type { ImpactCone } from "@contracts";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";
import type { RefObject } from "react";

import { stateStore } from "../state";
import { getActiveTheme } from "../theme";

import {
  frameCamera,
  includeNewNodes,
  nodesInViewport,
  prefersReducedMotion,
  ZOOM_RANGE,
  zoomBy,
} from "./camera";
import type { Camera, Framing } from "./camera";
import { graphElements } from "./elements";
import { exposeProbe } from "./probe";
import { showSelection } from "./selection";
import { edgeShape, graphStyles } from "./styles";
import { settle, updateGraph } from "./updateGraph";

cytoscape.use(fcose);

const NAVIGATION_QUIET_MS = 3000;

/** What the zoom buttons ask of the graph. Each call moves the camera and never a node. */
export interface GraphApi {
  zoomIn: () => void;
  zoomOut: () => void;
  /** The whole graph in view, as at the start. */
  fit: () => void;
}

const ZOOM_STEP = 1.25;

export interface GraphViewProps {
  /** The node with the selected ring. The app owns it; taps only report. */
  selectedId?: string | null;
  onNodeTap?: (nodeId: string) => void;
  /**
   * Whether the left panel is open. The graph's width changes with it, so when this flips the
   * camera moves (to the nodes in `frameIds` on open, back to the saved watch camera on close).
   */
  panelOpen?: boolean;
  /**
   * The change's nodes, framed with the cone at watch zoom or smaller down to the readable floor.
   * Read only when the panel target changes; incoming events move nothing. Empty falls back to the
   * selected node, then to the whole graph.
   */
  frameIds?: readonly string[] | null;
  /** The nodes that stay lit while everything else dims, as in review; null dims nothing. */
  litIds?: readonly string[] | null;
  /** The proposal's bounded cone; live updates do not carry one in v1. */
  cone?: ImpactCone | null;
  /** Changes only when the person opens a different panel target or enters review. */
  frameKey?: string | null;
  /** Reports the camera decision to the cone card without changing the lit set. */
  onFramed?: (framing: Framing | null) => void;
  /** The actual overlay box, measured independently from the column used for fitting. */
  coneCardRef?: RefObject<HTMLDivElement | null>;
  /** Filled while the graph exists, for the zoom buttons; empty before it is created and after. */
  apiRef?: RefObject<GraphApi | null>;
}

function reportFraming(
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
  const ids = [...(props.frameIds ?? []), ...props.cone.entries.map((entry) => entry.node_id)];
  const nodes = cy.nodes().filter((node) => ids.includes(node.id()));
  const visible = nodesInViewport(
    nodes.map((node) => ({
      id: node.id(),
      ...node.boundingBox({ includeLabels: true, includeOverlays: false }),
    })),
    framing,
    { x1: 0, y1: 0, x2: cy.width(), y2: cy.height() },
    card
      ? {
          x1: card.left - graphBox.left,
          y1: card.top - graphBox.top,
          x2: card.right - graphBox.left,
          y2: card.bottom - graphBox.top,
        }
      : undefined,
  );
  props.onFramed?.({ ...framing, outOfView: new Set(ids.filter((id) => !visible.has(id))) });
}

export function GraphView({
  selectedId = null,
  onNodeTap,
  panelOpen = false,
  litIds = null,
  frameIds = null,
  cone = null,
  frameKey = null,
  onFramed,
  coneCardRef,
  apiRef,
}: GraphViewProps = {}) {
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<Core | undefined>(undefined);
  const latest = useRef<GraphViewProps>({});
  const resync = useRef<(() => void) | undefined>(undefined);
  const framedFor = useRef(panelOpen);
  const framedKey = useRef(frameKey);
  const watchCamera = useRef<Camera | undefined>(undefined);
  const targetFrame = useRef<Framing | null>(null);
  const observeCard = useRef<(() => void) | undefined>(undefined);
  useEffect(() => {
    const relit = latest.current.litIds !== litIds || latest.current.cone !== cone;
    latest.current = {
      selectedId,
      onNodeTap,
      panelOpen,
      litIds,
      frameIds,
      cone,
      frameKey,
      onFramed,
      coneCardRef,
    };
    if (relit) resync.current?.();
    else if (graph.current) showSelection(graph.current, selectedId);
  }, [selectedId, onNodeTap, panelOpen, litIds, frameIds, cone, frameKey, onFramed, coneCardRef]);
  // Panel framing follows the person's choice; watch growth has its own guarded expansion.
  useEffect(() => {
    if (framedFor.current === panelOpen && framedKey.current === frameKey) return;
    const wasOpen = framedFor.current;
    framedFor.current = panelOpen;
    framedKey.current = frameKey;
    if (graph.current) {
      if (!wasOpen && panelOpen)
        watchCamera.current = { zoom: graph.current.zoom(), pan: { ...graph.current.pan() } };
      const { frameIds: framed, selectedId: selected, cone: impact } = latest.current;
      const focus = framed?.length ? framed : selected ? [selected] : [];
      const framing = frameCamera(graph.current, panelOpen ? focus : [], prefersReducedMotion(), {
        coneIds: panelOpen ? impact?.entries.map((entry) => entry.node_id) : undefined,
        watchZoom: watchCamera.current?.zoom,
        restoreCamera: panelOpen ? undefined : watchCamera.current,
      });
      targetFrame.current = framing;
      if (container.current)
        reportFraming(graph.current, container.current, latest.current, framing);
      observeCard.current?.();
      if (!panelOpen) watchCamera.current = undefined;
    }
  }, [panelOpen, frameKey]);
  useEffect(() => {
    const host = container.current;
    if (!host) return;
    const theme = getActiveTheme();
    let cancelled = false;
    let dispose: (() => void) | undefined;
    const create = () => {
      const cy = cytoscape({
        container: host,
        layout: { name: "preset" },
        boxSelectionEnabled: false,
        // The wheel and the pinch stay within the range the zoom buttons use.
        minZoom: ZOOM_RANGE.min,
        maxZoom: ZOOM_RANGE.max,
      });
      cy.style(
        graphStyles(
          theme,
          edgeShape(
            import.meta.env.DEV ? (typeof location === "undefined" ? "" : location.search) : "",
          ),
        ),
      );
      let interacting = false;
      let quietUntil = 0;
      const arriving = new Set<string>();
      cy.on("tapstart", () => {
        interacting = true;
        arriving.clear();
        cy.stop();
      });
      cy.on("tapend", () => {
        interacting = false;
        quietUntil = performance.now() + NAVIGATION_QUIET_MS;
      });
      // A node the person moved changes its frame's header and its edges' routes: settle them
      // when it lands, not on every pixel of the drag.
      cy.on("free", () => {
        settle(cy);
      });
      cy.on("dragpan scrollzoom pinchzoom", () => {
        arriving.clear();
        cy.stop();
        quietUntil = performance.now() + NAVIGATION_QUIET_MS;
      });
      const sync = () => {
        // A second addition can arrive before the first ease ends. Include both targets when
        // replacing that animation, rather than leaving the first addition outside the view.
        if (!cy.animated() || latest.current.panelOpen) arriving.clear();
        const existing = new Set(cy.nodes().map((node) => node.id()));
        const lit = latest.current.litIds;
        updateGraph(
          cy,
          graphElements(
            stateStore.getState().state,
            false,
            lit ? new Set(lit) : null,
            latest.current.cone,
          ),
        );
        showSelection(cy, latest.current.selectedId ?? null);
        if (
          existing.size > 0 &&
          !latest.current.panelOpen &&
          !interacting &&
          performance.now() >= quietUntil
        ) {
          const added = cy
            .nodes()
            .filter((node) => !existing.has(node.id()))
            .map((node) => node.id());
          if (added.length > 0) {
            for (const id of added) arriving.add(id);
            includeNewNodes(cy, [...arriving], prefersReducedMotion());
          }
        }
      };
      resync.current = sync;
      cy.on("tap", "node", (event) => {
        latest.current.onNodeTap?.((event.target as NodeSingular).id());
      });
      graph.current = cy;
      if (apiRef) {
        apiRef.current = {
          zoomIn: () => {
            zoomBy(cy, ZOOM_STEP, prefersReducedMotion());
          },
          zoomOut: () => {
            zoomBy(cy, 1 / ZOOM_STEP, prefersReducedMotion());
          },
          fit: () => {
            frameCamera(cy, [], prefersReducedMotion());
          },
        };
      }
      const unexpose = exposeProbe(host, cy);
      const unsubscribe = stateStore.subscribe(sync);
      sync();
      if (latest.current.panelOpen) {
        watchCamera.current = { zoom: cy.zoom(), pan: { ...cy.pan() } };
        const framing = frameCamera(cy, latest.current.frameIds ?? [], true, {
          coneIds: latest.current.cone?.entries.map((entry) => entry.node_id),
          watchZoom: watchCamera.current.zoom,
        });
        targetFrame.current = framing;
        reportFraming(cy, host, latest.current, framing);
      }
      const resize = new ResizeObserver(() => {
        cy.resize();
        const framing = targetFrame.current;
        if (framing && latest.current.cone)
          reportFraming(
            cy,
            host,
            latest.current,
            cy.animated() ? framing : { ...framing, zoom: cy.zoom(), pan: cy.pan() },
          );
      });
      let observedCard: HTMLDivElement | null | undefined;
      resize.observe(host);
      observeCard.current = () => {
        const card = latest.current.coneCardRef?.current;
        if (card === observedCard) return;
        observedCard = card;
        resize.disconnect();
        resize.observe(host);
        if (card) resize.observe(card);
      };
      observeCard.current();
      dispose = () => {
        if (apiRef) apiRef.current = null;
        unsubscribe();
        unexpose();
        resize.disconnect();
        resync.current = undefined;
        observeCard.current = undefined;
        graph.current = undefined;
        cy.destroy();
      };
    };
    // Create labels only after the font settles: reapplying identical styles
    // cannot invalidate Cytoscape's cached label measurements and textures.
    void document.fonts
      .load(`${theme["body-size"]} ${theme["text-font"]}`)
      .catch(() => undefined)
      .then(() => {
        if (!cancelled) create();
      });
    return () => {
      cancelled = true;
      dispose?.();
    };
  }, [apiRef]);
  return (
    <div
      ref={container}
      className="h-full w-full bg-ground"
      role="img"
      aria-label="Repository graph"
    />
  );
}
