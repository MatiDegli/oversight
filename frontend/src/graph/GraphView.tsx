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
  revealNode,
  startCamera,
  ZOOM_RANGE,
  zoomBy,
} from "./camera";
import type { Box, Camera, Framing } from "./camera";
import { FIT_PADDING } from "./geometry";
import { graphElements } from "./elements";
import { foldController } from "./foldController";
import { visibleId } from "./foldVisibility";
import { createBoxSource, DotField } from "./field";
import type { FieldInput } from "./field";
import { exposeProbe } from "./probe";
import { showSelection } from "./selection";
import { edgeShape, graphStyles } from "./styles";
import { settle } from "./updateGraph";

cytoscape.use(fcose);

const NAVIGATION_QUIET_MS = 3000;

/** What the zoom buttons ask of the graph. Each call moves the camera and never a node. */
export interface GraphApi {
  zoomIn: () => void;
  zoomOut: () => void;
  /** The whole graph in view, as at the start. */
  fit: () => void;
  /** Pans to a node the cone card lists as out of view, at the zoom it has. Only on a click. */
  reveal: (nodeId: string) => void;
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
   * The change's nodes, framed with the cone while keeping names and state words legible.
   * Read only when the panel target changes; incoming events move nothing. Empty falls back to the
   * selected node, then to the whole graph.
   */
  frameIds?: readonly string[] | null;
  /** The nodes that stay lit while everything else dims, as in review; null dims nothing. */
  litIds?: readonly string[] | null;
  /** The review proposal's cone, used for its card, framing and out-of-view report. */
  cone?: ImpactCone | null;
  /** Live preview: framed once on opening, then updates badges and dimming without following edits. */
  liveCone?: ImpactCone | null;
  /** Changes only when the person opens a different panel target or enters review. */
  frameKey?: string | null;
  /** Reports the camera decision to the cone card without changing the lit set. */
  onFramed?: (framing: Framing | null) => void;
  /** The actual overlay box, measured independently from the column used for fitting. */
  coneCardRef?: RefObject<HTMLDivElement | null>;
  /** Filled while the graph exists, for the zoom buttons; empty before it is created and after. */
  apiRef?: RefObject<GraphApi | null>;
  /** The initial watch area between the heading/notices and the bottom overlays. */
  startAreaRef?: RefObject<HTMLDivElement | null>;
  /** The lights of the dot field behind the graph and the mode that shapes them. */
  field?: FieldInput;
}

const NO_LIGHTS: FieldInput = { mode: "watch", lights: [] };

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
  const shown = ids.map((id) => visibleId(cy, id));
  const nodes = cy.nodes().filter((node) => shown.includes(node.id()));
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
  props.onFramed?.({
    ...framing,
    outOfView: new Set(ids.filter((id) => !visible.has(visibleId(cy, id)))),
  });
}

/** Snapshot panel geometry only when the person opens it, before the single camera move. */
function panelGeometry(host: HTMLElement, props: GraphViewProps): { occlusion?: Box; area?: Box } {
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
          area: {
            x1: (occlusion?.x2 ?? 0) + FIT_PADDING,
            y1: FIT_PADDING,
            x2: graph.width - FIT_PADDING,
            y2: Math.min(graph.height - FIT_PADDING, clear.bottom - graph.top),
          },
        }
      : {}),
  };
}

function openingCone(props: GraphViewProps): readonly string[] | undefined {
  const cone = props.cone ?? props.liveCone;
  return cone?.entries.filter((entry) => entry.distance <= cone.max_depth).map((e) => e.node_id);
}

export function GraphView({
  selectedId = null,
  onNodeTap,
  panelOpen = false,
  litIds = null,
  frameIds = null,
  cone = null,
  liveCone = null,
  frameKey = null,
  onFramed,
  coneCardRef,
  apiRef,
  startAreaRef,
  field = NO_LIGHTS,
}: GraphViewProps = {}) {
  const container = useRef<HTMLDivElement>(null);
  // What the dot field reads of the graph: the rendered boxes of the nodes it lights.
  const boxes = useRef(createBoxSource());
  const graph = useRef<Core | undefined>(undefined);
  const latest = useRef<GraphViewProps>({});
  const resync = useRef<(() => void) | undefined>(undefined);
  const framedFor = useRef(panelOpen);
  const framedKey = useRef(frameKey);
  const watchCamera = useRef<Camera | undefined>(undefined);
  const targetFrame = useRef<Framing | null>(null);
  const observeCard = useRef<(() => void) | undefined>(undefined);
  const reportViewport = useRef<(() => void) | undefined>(undefined);
  useEffect(() => {
    const relit =
      latest.current.litIds !== litIds ||
      latest.current.cone !== cone ||
      latest.current.liveCone !== liveCone ||
      latest.current.field?.mode !== field.mode;
    latest.current = {
      selectedId,
      onNodeTap,
      panelOpen,
      litIds,
      frameIds,
      cone,
      liveCone,
      frameKey,
      onFramed,
      coneCardRef,
      startAreaRef,
      field,
    };
    if (relit) resync.current?.();
    else if (graph.current) showSelection(graph.current, selectedId);
  }, [
    selectedId,
    onNodeTap,
    panelOpen,
    litIds,
    frameIds,
    cone,
    liveCone,
    frameKey,
    onFramed,
    coneCardRef,
    startAreaRef,
    field,
  ]);
  // Panel framing follows the person's choice; watch growth has its own guarded expansion.
  useEffect(() => {
    if (framedFor.current === panelOpen && framedKey.current === frameKey) return;
    const wasOpen = framedFor.current;
    framedFor.current = panelOpen;
    framedKey.current = frameKey;
    if (graph.current) {
      if (!wasOpen && panelOpen)
        watchCamera.current = { zoom: graph.current.zoom(), pan: { ...graph.current.pan() } };
      const { frameIds: framed, selectedId: selected } = latest.current;
      const focus = framed?.length ? framed : selected ? [selected] : [];
      const framing = frameCamera(graph.current, panelOpen ? focus : [], prefersReducedMotion(), {
        coneIds: panelOpen ? openingCone(latest.current) : undefined,
        ...(panelOpen && container.current ? panelGeometry(container.current, latest.current) : {}),
        restoreCamera: panelOpen ? undefined : watchCamera.current,
      });
      targetFrame.current = framing;
      reportViewport.current?.();
      observeCard.current?.();
      if (!panelOpen) watchCamera.current = undefined;
    }
  }, [panelOpen, frameKey]);
  useEffect(() => {
    const host = container.current;
    if (!host) return;
    const source = boxes.current;
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
      // Pan, zoom and animated camera moves can emit several viewport events per frame.
      // Read the actual camera once, never change it while reporting cone visibility.
      let viewportFrame: number | undefined;
      const reportSoon = () => {
        if (viewportFrame !== undefined) return;
        viewportFrame = requestAnimationFrame(() => {
          viewportFrame = undefined;
          const framing = targetFrame.current;
          if (framing)
            reportFraming(cy, host, latest.current, {
              ...framing,
              zoom: cy.zoom(),
              pan: { ...cy.pan() },
            });
        });
      };
      reportViewport.current = reportSoon;
      cy.on("viewport", reportSoon);
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
      const folding = foldController(cy);
      let startFrame: number | undefined;
      const mountedWithPanel = latest.current.panelOpen;
      const frameInitialPanel = () => {
        watchCamera.current = { zoom: cy.zoom(), pan: { ...cy.pan() } };
        const framing = frameCamera(cy, latest.current.frameIds ?? [], prefersReducedMotion(), {
          coneIds: openingCone(latest.current),
          ...panelGeometry(host, latest.current),
        });
        targetFrame.current = framing;
        reportSoon();
      };
      const sync = () => {
        // A second addition can arrive before the first ease ends. Include both targets when
        // replacing that animation, rather than leaving the first addition outside the view.
        if (!cy.animated() || latest.current.panelOpen) arriving.clear();
        const existing = new Set(cy.nodes().map((node) => node.id()));
        const state = stateStore.getState().state;
        const lit = latest.current.litIds;
        const foldsChanged = folding.render(
          state,
          graphElements(
            state,
            false,
            lit ? new Set(lit) : null,
            latest.current.cone ?? latest.current.liveCone,
            latest.current.field?.mode === "live-diff",
          ),
        );
        if (
          existing.size === 0 &&
          Object.keys(state.graph?.positions ?? {}).length > 0 &&
          startAreaRef?.current
        ) {
          // SSE may populate the graph before React commits the heading and legend. Measure
          // after that commit, so the initial camera sees the overlays the person will see.
          startFrame = requestAnimationFrame(() => {
            if (!startAreaRef.current || (latest.current.panelOpen && !mountedWithPanel)) return;
            const area = startAreaRef.current.getBoundingClientRect();
            const box = host.getBoundingClientRect();
            startCamera(cy, {
              x1: area.left - box.left,
              y1: area.top - box.top,
              x2: area.right - box.left,
              y2: area.bottom - box.top,
            });
            if (latest.current.panelOpen) frameInitialPanel();
          });
        }
        showSelection(cy, latest.current.selectedId ?? null);
        reportSoon();
        if (
          existing.size > 0 &&
          !foldsChanged &&
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
        const id = (event.target as NodeSingular).id();
        if (folding.toggle(id)) sync();
        else latest.current.onNodeTap?.(id);
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
          reveal: (nodeId) => {
            const target = revealNode(cy, nodeId, prefersReducedMotion());
            if (!target) return;
            // The cone card lists what is out of view from the camera it is told of.
            const framing = { ...target, framed: new Set<string>(), outOfView: new Set<string>() };
            targetFrame.current = framing;
            reportSoon();
          },
        };
      }
      const unexpose = exposeProbe(host, cy);
      source.attach(cy);
      const unsubscribe = stateStore.subscribe(sync);
      sync();
      if (latest.current.panelOpen && startFrame === undefined) frameInitialPanel();
      const resize = new ResizeObserver(() => {
        cy.resize();
        reportSoon();
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
        source.detach();
        unsubscribe();
        unexpose();
        resize.disconnect();
        // Card observation must leave the initial overlay-aware camera scheduled until unmount.
        if (startFrame !== undefined) cancelAnimationFrame(startFrame);
        resync.current = undefined;
        observeCard.current = undefined;
        graph.current = undefined;
        reportViewport.current = undefined;
        if (viewportFrame !== undefined) cancelAnimationFrame(viewportFrame);
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
  }, [apiRef, startAreaRef]);
  // The dot field is its own canvas under Cytoscape's transparent one: first in the tree, so
  // everything the graph draws is painted over it.
  return (
    <div className="relative h-full w-full bg-ground">
      <DotField source={boxes} mode={field.mode} lights={field.lights} />
      <div
        ref={container}
        className="relative h-full w-full"
        role="img"
        aria-label="Repository graph"
      />
    </div>
  );
}
