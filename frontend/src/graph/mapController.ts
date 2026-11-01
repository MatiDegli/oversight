import cytoscape, { type Core } from "cytoscape";
import { useRef } from "react";
import type { RefObject } from "react";
import { stateStore } from "../state";
import { decisionsLabel, mapEvidence, testedLabel } from "./mapEvidence";
import { getActiveTheme } from "../theme";
import {
  centerMapCards,
  fitPanelCamera,
  frameCamera,
  prefersReducedMotion,
  startCamera,
  zoomBy,
} from "./camera";
import type { Camera } from "./camera";
import type { GraphElement } from "./elements";
import { packFallback } from "./fallbackLayout";
import { FIT_PADDING } from "./geometry";
import type { GraphViewProps } from "./GraphView";
import type { MapFrame } from "./MapLayer";
import { MAP_MIN_MODULES, nextLevel, targetLevel } from "./mapLevel";
import { mapCalls, mapHit } from "./mapLines";
import type { MapHover } from "./mapLines";
import { mapModel, panelBox } from "./mapModel";
import { settle } from "./updateGraph";

/** Measure only after GraphView's font gate; fitting and drawing then share header bounds. */
function measureHeaders(model: MapFrame["model"]) {
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return;
  const font = getActiveTheme()["text-font"];
  for (const group of model.groups) {
    context.font = `600 15px ${font}`;
    const name = context.measureText(group.label).width;
    context.font = `400 13px ${font}`;
    const count = context.measureText(`${String(group.modules.length)} modules`).width;
    const widths = [name, count];
    if (group.rings?.length) widths.push(group.rings.length * 24 - 4);
    context.font = `600 12px ${font}`;
    const decision = decisionsLabel(group.decisions ?? 0);
    if (decision) widths.push(context.measureText(decision).width + 31);
    context.font = `400 13px ${font}`;
    if (group.coneCount) {
      const suffix = context.measureText(` in ${group.label}`).width;
      context.font = `600 13px ${font}`;
      widths.push(
        context.measureText(`${String(group.coneCount)} of ${String(group.coneTotal)}`).width +
          suffix,
      );
    } else {
      const tested = testedLabel(group.tested ?? 0, group.id === "tests");
      if (tested) widths.push(context.measureText(tested).width);
    }
    group.headerItemWidths = widths;
    for (const module of group.modules) {
      context.font = `600 13px ${font}`;
      module.nameWidth = context.measureText(module.fullName ?? module.name).width;
    }
  }
}

/** Owns the opt-in layer and camera intent; events update its model without changing level. */
export interface MapOptions {
  map?: boolean;
  onMapLevel?: (level: MapFrame["level"]) => void;
}

export function useMapController(
  host: RefObject<HTMLDivElement | null>,
  latest: RefObject<GraphViewProps>,
) {
  const control = useRef(createMapController(host, latest));
  // eslint-disable-next-line react-hooks/refs -- Stable external-store handle; the layer subscribes to its snapshot.
  return control.current;
}

export type MapController = ReturnType<typeof createMapController>;

function createMapController(
  host: RefObject<HTMLDivElement | null>,
  latest: RefObject<GraphViewProps>,
) {
  let frame: MapFrame = {
    model: { groups: [], cellWidth: 446, cellHeight: 220 },
    zoom: 1,
    pan: { x: 0, y: 0 },
    level: "cards",
  };
  const listeners = new Set<() => void>();
  let cy: Core | undefined;
  let model: MapFrame["model"] = { groups: [], cellWidth: 446, cellHeight: 220 };
  let level: MapFrame["level"] = "cards";
  let calls: MapFrame["calls"];
  let hover: MapHover | undefined;
  let pointerFrame = 0;
  let pointer: { x: number; y: number } | undefined;
  let clickApi:
    | {
        folding: { toggle: (id: string) => boolean };
        sync: () => void;
        geometry: () => Parameters<typeof frameCamera>[3];
      }
    | undefined;
  let down: { x: number; y: number } | undefined,
    dragged = false;
  const surface = (event: MouseEvent) =>
    host.current?.contains(event.target as Node) || event.target === host.current?.parentElement;
  const active = () => latest.current.map && level === "map";
  const setHover = (hit?: MapHover) => {
    if (hover?.kind === hit?.kind && hover?.id === hit?.id) return;
    hover = hit;
    frame = { ...frame, hover };
    for (const listener of listeners) listener();
  };
  const point = (event: MouseEvent) => {
    const box = host.current?.getBoundingClientRect();
    return box ? { x: event.clientX - box.left, y: event.clientY - box.top } : undefined;
  };
  const pointermove = (event: PointerEvent) => {
    if (!active()) return;
    pointer = point(event);
    if (down && pointer && cy) {
      const dx = pointer.x - down.x,
        dy = pointer.y - down.y;
      if (Math.hypot(dx, dy) > 3 || dragged) {
        dragged = true;
        cy.panBy({ x: dx, y: dy });
        down = pointer;
      }
    }
    if (pointerFrame) return;
    pointerFrame = requestAnimationFrame(() => {
      pointerFrame = 0;
      if (active()) setHover(pointer && mapHit(pointer, frame));
    });
  };
  const pointerleave = () => {
    if (!active()) return;
    cancelAnimationFrame(pointerFrame);
    pointerFrame = 0;
    pointer = undefined;
    setHover();
  };
  const mousedown = (event: MouseEvent) => {
    if (!active() || !surface(event) || event.button !== 0) return;
    event.preventDefault();
    // Cytoscape explicitly blurs on mousedown; own the map gesture before it reaches its canvas.
    event.stopPropagation();
    down = pointer ?? point(event);
    dragged = false;
  };
  const mousemove = (event: MouseEvent) => {
    if (active() && surface(event)) event.stopPropagation();
  };
  const mouseup = () => {
    down = undefined;
  };
  const click = (event: MouseEvent) => {
    if (!active() || !cy || !surface(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (dragged) return;
    const p = point(event),
      hit = p && mapHit(p, frame);
    if (!hit || !clickApi) return;
    const graph = cy,
      api = clickApi;
    if (hit.kind === "module" && graph.getElementById(hit.id).hasClass("folded")) {
      api.folding.toggle(hit.id);
      api.sync();
    }
    const ids =
      hit.kind === "module"
        ? [hit.id]
        : (model.groups.find((g) => g.id === hit.id)?.modules.map((m) => m.id) ?? []);
    explicit(() =>
      centerMapCards(
        graph,
        ids,
        prefersReducedMotion(),
        api.geometry()?.area,
        hit.kind === "module",
      ),
    );
  };
  const pannable = new Set<string>();
  const shownModules = () => cy?.nodes(".module").length ?? 0;
  const publish = () => {
    if (!cy || !latest.current.map) return;
    if (shownModules() < MAP_MIN_MODULES) level = "cards";
    host.current?.setAttribute("data-map-level", level);
    latest.current.onMapLevel?.(level);
    if (level !== "map") hover = undefined;
    frame = { model, level, zoom: cy.zoom(), pan: { ...cy.pan() }, calls, hover };
    cy.autoungrabify(level === "map");
    cy.boxSelectionEnabled(false);
    if (level === "map") {
      for (const node of cy.nodes().filter((n) => !n.pannable())) {
        pannable.add(node.id());
        node.panify();
      }
    } else {
      for (const id of pannable) cy.getElementById(id).unpanify();
      pannable.clear();
    }
    for (const listener of listeners) listener();
    host.current?.dispatchEvent(new CustomEvent("graphrender"));
  };
  const wheel = (event: WheelEvent) => {
    if (
      !cy ||
      !latest.current.map ||
      level !== "map" ||
      host.current?.contains(event.target as Node)
    )
      return;
    event.preventDefault();
    const box = host.current?.getBoundingClientRect();
    if (!box) return;
    cy.zoom({
      level: Math.min(
        cy.maxZoom(),
        Math.max(cy.minZoom(), cy.zoom() * Math.pow(10, -event.deltaY / 250)),
      ),
      renderedPosition: { x: event.clientX - box.left, y: event.clientY - box.top },
    });
    navigate();
  };
  const viewport = () => {
    publish();
  };
  const navigate = () => {
    if (!cy || !latest.current.map) return;
    level = nextLevel(level, cy.zoom(), shownModules());
    publish();
  };
  // Viewport events only follow the camera. An explicit move therefore holds its target level
  // throughout animation; hysteresis runs again only for the person's next navigation gesture.
  const hold = (zoom: number) => {
    if (latest.current.map) {
      level = targetLevel(zoom, shownModules());
      publish();
    }
  };
  const explicit = <T>(move: () => T): T => {
    if (!latest.current.map) return move();
    const target = move();
    hold((target as Camera | null | undefined)?.zoom ?? cy?.zoom() ?? 1);
    return target;
  };
  return {
    getFrame: () => frame,
    level: () => level,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    attach(graph: Core) {
      cy = graph;
      const stage = host.current?.parentElement;
      stage?.addEventListener("mousedown", mousedown, true);
      stage?.addEventListener("mousemove", mousemove, true);
      stage?.addEventListener("click", click, true);
      stage?.ownerDocument.defaultView?.addEventListener("mouseup", mouseup);
      stage?.addEventListener("pointermove", pointermove);
      stage?.addEventListener("pointerleave", pointerleave);
      cy.on("viewport", viewport);
      cy.on("scrollzoom pinchzoom", navigate);
      host.current?.parentElement?.addEventListener("wheel", wheel, { passive: false });
      return () => {
        stage?.removeEventListener("mousedown", mousedown, true);
        stage?.removeEventListener("mousemove", mousemove, true);
        stage?.removeEventListener("click", click, true);
        stage?.ownerDocument.defaultView?.removeEventListener("mouseup", mouseup);
        stage?.removeEventListener("pointermove", pointermove);
        stage?.removeEventListener("pointerleave", pointerleave);
        cancelAnimationFrame(pointerFrame);
        host.current?.parentElement?.removeEventListener("wheel", wheel);
        cy?.off("viewport", viewport);
        cy?.off("scrollzoom pinchzoom", navigate);
        cy = undefined;
      };
    },
    sync(elements: readonly GraphElement[]) {
      if (!latest.current.map) return;
      model = mapEvidence(
        mapModel(elements, getActiveTheme()),
        elements,
        stateStore.getState().state,
        latest.current.cone ?? latest.current.liveCone,
      );
      calls = mapCalls(elements);
      measureHeaders(model);
      publish();
    },
    explicit,
    zoom(graph: Core, factor: number) {
      if (latest.current.map) {
        level = nextLevel(level, graph.zoom() * factor, shownModules());
      }
      zoomBy(graph, factor, prefersReducedMotion());
      publish();
    },
    frame(graph: Core, ids: readonly string[], geometry: Parameters<typeof frameCamera>[3]) {
      return this.explicit(() => frameCamera(graph, ids, prefersReducedMotion(), geometry));
    },
    start(graph: Core, area: Parameters<typeof startCamera>[1]) {
      this.explicit(() => {
        startCamera(graph, area);
      });
    },
    api(
      folding: {
        detail: (level: "Modules" | "Functions" | "Details") => void;
        toggle: (id: string) => boolean;
      },
      sync: () => void,
      geometry: () => Parameters<typeof frameCamera>[3],
    ) {
      clickApi = { folding, sync, geometry };
      return {
        fit: () => {
          this.fit(geometry());
        },
        detail: (level: "Modules" | "Functions" | "Details") => {
          folding.detail(level);
          sync();
          if (level === "Modules") this.fit(geometry());
        },
      };
    },
    fit(geometry: Parameters<typeof frameCamera>[3]) {
      const graph = cy;
      if (!graph) return;
      this.explicit(() => {
        const cards = frameCamera(graph, [], prefersReducedMotion(), geometry);
        if (!cards || !latest.current.map || targetLevel(cards.zoom, shownModules()) === "cards")
          return cards;
        return fitPanelCamera(
          graph,
          (zoom) => model.groups.map((g) => panelBox(g, zoom, model.groups)),
          prefersReducedMotion(),
          geometry?.area,
          cards.zoom,
        );
      });
    },
  };
}

let fallbackLayout: Promise<void> | undefined;
let fallbackReady = false;
const failedLayouts = new WeakSet<Core>();

/** Replace only this graph's provisional layout, once, after a successful solver retry. */
function recoverLayout(cy: Core) {
  if (cy.destroyed() || !failedLayouts.delete(cy)) return;
  const options = { name: "fcose", animate: false, fit: true, padding: FIT_PADDING };
  cy.layout(options).run();
  packFallback(cy, getActiveTheme());
  settle(cy);
}

/** Presets need no solver download. Wait for fcose only before a graph's first unplaced nodes. */
export function prepareGraphLayout(
  cy: Core,
  elements: readonly GraphElement[],
  retry: (fallback?: GraphElement[]) => void,
) {
  if (fallbackReady) {
    recoverLayout(cy);
    return true;
  }
  if (
    (cy.nodes().nonempty() && !failedLayouts.has(cy)) ||
    elements.some((e) => e.position !== undefined)
  )
    return true;
  if (!elements.some((e) => e.group === "nodes")) return true;
  fallbackLayout ??= import("cytoscape-fcose").then(({ default: fcose }) => {
    cytoscape.use(fcose);
    fallbackReady = true;
  });
  void fallbackLayout.then(
    () => {
      if (!cy.destroyed()) {
        retry();
        recoverLayout(cy);
      }
    },
    () => {
      fallbackLayout = undefined;
      failedLayouts.add(cy);
      // Draw a provisional grid without mutating the cached snapshot or caching the rejection.
      // A later sync retries the solver even though this graph now has visible nodes.
      if (!cy.destroyed()) {
        let root = 0;
        retry(
          elements.map((e) => {
            if (e.group !== "nodes" || e.data.parent) return e;
            const i = root++;
            return { ...e, position: { x: (i % 9) * 446, y: Math.floor(i / 9) * 220 } };
          }),
        );
        packFallback(cy, getActiveTheme());
        settle(cy);
      }
    },
  );
  return false;
}
