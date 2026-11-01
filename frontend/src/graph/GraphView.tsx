import cytoscape from "cytoscape";
import type { Core, EdgeSingular, NodeSingular } from "cytoscape";
import type { ImpactCone } from "@contracts";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";
import type { RefObject } from "react";

import { stateStore } from "../state";
import { getActiveTheme } from "../theme";

import {
  frameCamera,
  includeNewNodes,
  prefersReducedMotion,
  revealNode,
  startCamera,
  ZOOM_RANGE,
  zoomBy,
} from "./camera";
import type { Camera, Framing } from "./camera";
import { elementSnapshot } from "./elementSnapshot";
import type { MeasuredWord } from "./elements";
import { foldController } from "./foldController";
import { createBoxSource, DotField } from "./field";
import type { FieldInput } from "./field";
import { exposeProbe } from "./probe";
import { showNodeHover, showSelection } from "./selection";
import { setScratch } from "./scratch";
import { edgeShape, graphStyles } from "./styles";
import { openingCone, panelGeometry, reportFraming } from "./viewFraming";
import { applyRoutes, dragRoutes } from "./routes";
import { RoutingOmissions } from "./RoutingOmissions";

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
  detail?: (level: "Modules" | "Functions" | "Details") => void;
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
  /** Usage resizes the viewport while retaining the person's current camera. */
  preserveCamera?: boolean;
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
  measured?: ReadonlyMap<string, MeasuredWord | null> | null;
  /** Live preview: framed once on opening, then updates badges and dimming without following edits. */
  liveCone?: ImpactCone | null;
  /** Changes only when the person opens a different panel target or enters review. */
  frameKey?: string | null;
  /** Reports the camera decision to the cone card without changing the lit set. */
  onFramed?: (framing: Framing | null) => void;
  /** The actual overlay box, measured independently from the column used for fitting. */
  coneCardRef?: RefObject<HTMLDivElement | null>;
  /** Retained by the shell so a remounted cone card receives the last routing count. */
  onRoutingOmissions?: (count: number) => void;
  /** Filled while the graph exists, for the zoom buttons; empty before it is created and after. */
  apiRef?: RefObject<GraphApi | null>;
  /** The initial watch area between the heading/notices and the bottom overlays. */
  startAreaRef?: RefObject<HTMLDivElement | null>;
  /** The lights of the dot field behind the graph and the mode that shapes them. */
  field?: FieldInput;
  onZoom?: (percent: number) => void;
}

const NO_LIGHTS: FieldInput = { mode: "watch", lights: [] };

export function GraphView({
  selectedId = null,
  onNodeTap,
  panelOpen = false,
  preserveCamera = false,
  litIds = null,
  frameIds = null,
  cone = null,
  measured = null,
  liveCone = null,
  frameKey = null,
  onFramed,
  coneCardRef,
  onRoutingOmissions,
  apiRef,
  startAreaRef,
  field = NO_LIGHTS,
  onZoom,
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
      latest.current.measured !== measured ||
      latest.current.liveCone !== liveCone ||
      latest.current.field?.mode !== field.mode;
    latest.current = {
      selectedId,
      onNodeTap,
      panelOpen,
      preserveCamera,
      litIds,
      frameIds,
      cone,
      measured,
      liveCone,
      frameKey,
      onFramed,
      coneCardRef,
      startAreaRef,
      field,
      onZoom,
    };
    if (relit) resync.current?.();
    else if (graph.current) showSelection(graph.current, selectedId);
  }, [
    selectedId,
    onNodeTap,
    panelOpen,
    preserveCamera,
    litIds,
    frameIds,
    cone,
    measured,
    liveCone,
    frameKey,
    onFramed,
    coneCardRef,
    startAreaRef,
    field,
    onZoom,
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
      if (preserveCamera) {
        graph.current.stop();
        graph.current.resize();
        targetFrame.current = null;
        reportViewport.current?.();
        observeCard.current?.();
        return;
      }
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
  }, [panelOpen, frameKey, preserveCamera]);
  useEffect(() => {
    const host = container.current;
    if (!host) return;
    const source = boxes.current;
    const theme = getActiveTheme();
    const fonts = [
      `${theme["body-size"]} ${theme["text-font"]}`,
      `${String(theme["frame-title-weight"])} ${theme["frame-title-size"]} ${theme["text-font"]}`,
      `${String(theme["caption-weight"])} ${theme["frame-count-size"]} ${theme["text-font"]}`,
    ];
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
      const shape = edgeShape(
        import.meta.env.DEV ? (typeof location === "undefined" ? "" : location.search) : "",
      );
      cy.style(graphStyles(theme, shape));
      // A failed initial load may recover later. Replace the layer caches once the faces load.
      const refreshFonts = () => {
        if (!fonts.every((font) => document.fonts.check(font))) return;
        cy.style(graphStyles(theme, shape));
        document.fonts.removeEventListener("loadingdone", refreshFonts);
      };
      if (!fonts.every((font) => document.fonts.check(font)))
        document.fonts.addEventListener("loadingdone", refreshFonts);
      // Pan, zoom and animated camera moves can emit several viewport events per frame.
      // Read the actual camera once, never change it while reporting cone visibility.
      let viewportFrame: number | undefined;
      const reportSoon = () => {
        if (viewportFrame !== undefined) return;
        viewportFrame = requestAnimationFrame(() => {
          viewportFrame = undefined;
          latest.current.onZoom?.(Math.round(cy.zoom() * 100));
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
      cy.on("mouseover mouseout", "edge", (event) => {
        cy.batch(() => {
          const edge = event.target as EdgeSingular;
          const enter = event.type === "mouseover";
          edge.toggleClass("count-hover", enter);
          cy.nodes(".module").toggleClass("edge-shield", enter && edge.hasClass("possible"));
        });
      });
      cy.on("mouseover mouseout", "node", (event) => {
        showNodeHover(cy, event.type === "mouseover" ? (event.target as NodeSingular).id() : null);
      });
      const clearPointer = () => {
        cy.batch(() => {
          cy.edges(".count-hover, .node-hover").removeClass("count-hover node-hover");
          cy.nodes(".edge-shield").removeClass("edge-shield");
        });
      };
      host.addEventListener("pointerleave", clearPointer);
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
      let dragFrame: number | undefined;
      const translations = new Map<string, { x: number; y: number }>();
      const reroute = () => {
        if (dragFrame !== undefined) return;
        dragFrame = requestAnimationFrame(() => {
          dragFrame = undefined;
          const start = performance.now();
          for (const [id, delta] of translations) dragRoutes(cy, id, delta.x, delta.y, theme);
          translations.clear();
          const samples = (cy.scratch("dragCosts") as number[] | undefined) ?? [];
          samples.push(performance.now() - start);
          setScratch(cy, "dragCosts", samples.slice(-120));
        });
      };
      const moved = new Map<string, { x: number; y: number }>();
      cy.on("grab", "node.module", (event) => {
        const node = event.target as NodeSingular;
        moved.set(node.id(), { ...node.position() });
      });
      cy.on("drag", "node.module", (event) => {
        const node = event.target as NodeSingular;
        const previous = moved.get(node.id());
        if (previous) {
          const dx = node.position("x") - previous.x,
            dy = node.position("y") - previous.y;
          folding.move(node.id(), dx, dy);
          const pending = translations.get(node.id()) ?? { x: 0, y: 0 };
          translations.set(node.id(), { x: pending.x + dx, y: pending.y + dy });
        }
        moved.set(node.id(), { ...node.position() });
        reroute();
      });
      cy.on("free", (event) => {
        if (translations.size) reroute();
        else if (!moved.has((event.target as NodeSingular).id())) applyRoutes(cy, theme);
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
        if (latest.current.preserveCamera) {
          cy.resize();
          return;
        }
        const framing = frameCamera(cy, latest.current.frameIds ?? [], prefersReducedMotion(), {
          coneIds: openingCone(latest.current),
          ...panelGeometry(host, latest.current),
        });
        targetFrame.current = framing;
        reportSoon();
      };
      const coveredWidth = () => {
        const overlays = latest.current.startAreaRef?.current?.parentElement;
        return overlays
          ? Number.parseFloat(
              getComputedStyle(overlays).getPropertyValue("--graph-overlay-occlusion"),
            ) || 0
          : 0;
      };
      const snapshot = elementSnapshot();
      const sync = () => {
        // A second addition can arrive before the first ease ends. Include both targets when
        // replacing that animation, rather than leaving the first addition outside the view.
        if (!cy.animated() || latest.current.panelOpen) arriving.clear();
        const existing = new Set(cy.nodes().map((node) => node.id()));
        const state = stateStore.getState().state;
        const foldsChanged = folding.render(state, snapshot(state, latest.current));
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
            includeNewNodes(cy, [...arriving], prefersReducedMotion(), coveredWidth());
          }
        }
      };
      resync.current = sync;
      cy.on("tap", "node", (event) => {
        const node = event.target as NodeSingular;
        const id = node.id();
        if (node.isParent() && node.is(".module, .class")) {
          const top = node.boundingBox({ includeLabels: false, includeOverlays: false }).y1;
          const height = Number.parseFloat(
            theme[node.hasClass("module") ? "frame-pad-top" : "class-pad-top"],
          );
          if (event.position.y > top + height) return;
        }
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
            frameCamera(cy, [], prefersReducedMotion(), panelGeometry(host, latest.current));
          },
          detail: (level) => {
            folding.detail(level);
            sync();
            if (level === "Modules")
              frameCamera(cy, [], prefersReducedMotion(), panelGeometry(host, latest.current));
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
        host.removeEventListener("pointerleave", clearPointer);
        document.fonts.removeEventListener("loadingdone", refreshFonts);
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
        if (dragFrame !== undefined) cancelAnimationFrame(dragFrame);
        cy.destroy();
      };
    };
    // Create labels only after the font settles: reapplying identical styles
    // cannot invalidate Cytoscape's cached label measurements and textures.
    // Header layers are built only after the title and count faces have settled too.
    void Promise.all(fonts.map((font) => document.fonts.load(font)))
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
      <DotField
        source={boxes}
        mode={field.mode}
        lights={field.lights}
        merges={field.merges}
        modules={field.modules}
      />
      <div
        ref={container}
        className="relative h-full w-full"
        role="img"
        aria-label="Repository graph"
      />
      <RoutingOmissions hostRef={container} inCone={cone !== null} onCount={onRoutingOmissions} />
    </div>
  );
}
