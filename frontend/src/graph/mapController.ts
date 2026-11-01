import cytoscape, { type Core } from "cytoscape";
import { useRef } from "react";
import type { RefObject } from "react";
import { getActiveTheme } from "../theme";
import { fitPanelCamera, frameCamera, prefersReducedMotion, startCamera, zoomBy } from "./camera";
import type { Camera } from "./camera";
import type { GraphElement } from "./elements";
import type { GraphViewProps } from "./GraphView";
import type { MapFrame } from "./MapLayer";
import { MAP_MIN_MODULES, nextLevel, targetLevel } from "./mapLevel";
import { mapModel, panelBox } from "./mapModel";

/** Measure only after GraphView's font gate; fitting and drawing then share header bounds. */
function measureHeaders(model: MapFrame["model"]) {
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return;
  const font = getActiveTheme()["text-font"];
  for (const group of model.groups) {
    context.font = `600 15px ${font}`;
    const name = context.measureText(group.label).width;
    context.font = `400 13px ${font}`;
    group.headerWidth =
      name + context.measureText(`${String(group.modules.length)} modules`).width + 36;
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
  const pannable = new Set<string>();
  const shownModules = () => cy?.nodes(".module").length ?? 0;
  const publish = () => {
    if (!cy || !latest.current.map) return;
    if (shownModules() < MAP_MIN_MODULES) level = "cards";
    host.current?.setAttribute("data-map-level", level);
    latest.current.onMapLevel?.(level);
    frame = { model, level, zoom: cy.zoom(), pan: { ...cy.pan() } };
    cy.autoungrabify(level === "map");
    cy.autounselectify(level === "map");
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
      cy.on("viewport", viewport);
      cy.on("scrollzoom pinchzoom", navigate);
      host.current?.parentElement?.addEventListener("wheel", wheel, { passive: false });
      return () => {
        host.current?.parentElement?.removeEventListener("wheel", wheel);
        cy?.off("viewport", viewport);
        cy?.off("scrollzoom pinchzoom", navigate);
        cy = undefined;
      };
    },
    sync(elements: readonly GraphElement[]) {
      if (!latest.current.map) return;
      model = mapModel(elements, getActiveTheme());
      measureHeaders(model);
      publish();
    },
    explicit<T>(move: () => T): T {
      if (!latest.current.map) return move();
      const target = move();
      hold((target as Camera | null | undefined)?.zoom ?? cy?.zoom() ?? 1);
      return target;
    },
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
      folding: { detail: (level: "Modules" | "Functions" | "Details") => void },
      sync: () => void,
      geometry: () => Parameters<typeof frameCamera>[3],
    ) {
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

/** Presets need no solver download. Wait for fcose only before a graph's first unplaced nodes. */
export function prepareGraphLayout(
  cy: Core,
  elements: readonly GraphElement[],
  retry: (fallback?: GraphElement[]) => void,
) {
  if (
    fallbackReady ||
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
      failedLayouts.delete(cy);
      if (!cy.destroyed()) retry();
    },
    () => {
      fallbackLayout = undefined;
      failedLayouts.add(cy);
      // Draw a provisional grid without mutating the cached snapshot or caching the rejection.
      // A later sync retries the solver even though this graph now has visible nodes.
      if (!cy.destroyed())
        retry(
          elements.map((e, i) => ({
            ...e,
            position: { x: (i % 9) * 446, y: Math.floor(i / 9) * 220 },
          })),
        );
    },
  );
  return false;
}
