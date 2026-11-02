import cytoscape, { type Core } from "cytoscape";
import { useRef } from "react";
import type { RefObject } from "react";
import { stateStore } from "../state";
import { decisionsLabel, mapEvidence, moduleOwners, testedLabel } from "./mapEvidence";
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
import { mapCalls } from "./mapLinesModel";
import { createMapPointer } from "./mapPointer";
import type { MapHover } from "./mapLinesModel";
import { mapModel, panelBox } from "./mapModel";
import { settle } from "./updateGraph";

type Geometry = Parameters<typeof frameCamera>[3];
type Explicit = <T>(move: () => T) => T;

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

interface MapRuntime {
  host: RefObject<HTMLDivElement | null>;
  latest: RefObject<GraphViewProps>;
  cy?: Core;
  frame: MapFrame;
  owners: ReadonlyMap<string, string>;
  listeners: Set<() => void>;
  pannable: Set<string>;
  clickApi?: {
    folding: { toggle: (id: string) => boolean };
    sync: () => void;
    geometry: () => Geometry;
  };
}
const notify = (runtime: MapRuntime) => {
  for (const listener of runtime.listeners) listener();
};

function publishMap(runtime: MapRuntime, level: MapFrame["level"]) {
  const { cy, host, latest, pannable } = runtime;
  if (!cy || !latest.current.map) return;
  host.current?.setAttribute("data-map-level", level);
  latest.current.onMapLevel?.(level);
  runtime.frame = {
    ...runtime.frame,
    level,
    zoom: cy.zoom(),
    pan: { ...cy.pan() },
    hover: level === "map" ? runtime.frame.hover : undefined,
  };
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
  notify(runtime);
  host.current?.dispatchEvent(new CustomEvent("graphrender"));
}

function openMapHit(runtime: MapRuntime, explicit: Explicit, hit: MapHover) {
  const graph = runtime.cy,
    api = runtime.clickApi;
  if (!graph || !api) return;
  if (hit.kind === "module" && graph.getElementById(hit.id).hasClass("folded")) {
    api.folding.toggle(hit.id);
    api.sync();
  }
  const ids =
    hit.kind === "module"
      ? [hit.id]
      : (runtime.frame.model.groups.find((g) => g.id === hit.id)?.modules.map((m) => m.id) ?? []);
  explicit(() =>
    centerMapCards(graph, ids, prefersReducedMotion(), api.geometry()?.area, hit.kind === "module"),
  );
}

function mapCommands(runtime: MapRuntime, publish: () => void) {
  const { latest } = runtime;
  const shownModules = () => runtime.cy?.nodes(".module").length ?? 0;
  let level: MapFrame["level"] = "cards";
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
    hold((target as Camera | null | undefined)?.zoom ?? runtime.cy?.zoom() ?? 1);
    return target;
  };
  return {
    ...mapFraming(runtime, explicit),
    cameraLevel: () => (level = shownModules() < MAP_MIN_MODULES ? "cards" : level),
    navigate: () => {
      const cy = runtime.cy;
      if (!cy || !latest.current.map) return;
      level = nextLevel(level, cy.zoom(), shownModules());
      publish();
    },
    zoom(graph: Core, factor: number) {
      if (latest.current.map) {
        level = nextLevel(level, graph.zoom() * factor, shownModules());
      }
      zoomBy(graph, factor, prefersReducedMotion());
      publish();
    },
    frame(graph: Core, ids: readonly string[], geometry: Geometry) {
      return this.explicit(() => frameCamera(graph, ids, prefersReducedMotion(), geometry));
    },
    start(graph: Core, area: Parameters<typeof startCamera>[1]) {
      this.explicit(() => {
        startCamera(graph, area);
      });
    },
  };
}

function mapFraming(runtime: MapRuntime, explicit: Explicit) {
  const { latest } = runtime;
  const shownModules = () => runtime.cy?.nodes(".module").length ?? 0;
  return {
    explicit,
    api(
      folding: {
        detail: (level: "Modules" | "Functions" | "Details") => void;
        toggle: (id: string) => boolean;
      },
      sync: () => void,
      geometry: () => Geometry,
    ) {
      runtime.clickApi = { folding, sync, geometry };
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
    fit(geometry: Geometry) {
      const graph = runtime.cy;
      if (!graph) return;
      this.explicit(() => {
        const cards = frameCamera(graph, [], prefersReducedMotion(), geometry);
        if (!cards || !latest.current.map || targetLevel(cards.zoom, shownModules()) === "cards")
          return cards;
        const { model } = runtime.frame;
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

function createMapController(
  host: RefObject<HTMLDivElement | null>,
  latest: RefObject<GraphViewProps>,
) {
  const runtime: MapRuntime = {
    host,
    latest,
    frame: {
      model: { groups: [], cellWidth: 446, cellHeight: 220 },
      zoom: 1,
      pan: { x: 0, y: 0 },
      level: "cards",
    },
    owners: new Map(),
    listeners: new Set(),
    pannable: new Set(),
  };
  const publish = () => {
    publishMap(runtime, commands.cameraLevel());
    pointer.refresh();
  };
  const commands = mapCommands(runtime, publish);
  const pointer = createMapPointer({
    host,
    active: () => latest.current.map && runtime.frame.level === "map",
    graph: () => runtime.cy,
    frame: () => runtime.frame,
    setHover: (hover) => {
      if (runtime.frame.hover?.kind === hover?.kind && runtime.frame.hover?.id === hover?.id)
        return;
      runtime.frame = { ...runtime.frame, hover };
      notify(runtime);
    },
    open: (hit) => {
      openMapHit(runtime, (move) => commands.explicit(move), hit);
    },
    navigate: commands.navigate,
  });
  return {
    ...commands,
    getFrame: () => runtime.frame,
    moduleOwners: () => runtime.owners,
    level: () => (latest.current.map ? runtime.frame.level : "cards"),
    subscribe: (listener: () => void) => {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    attach(graph: Core) {
      runtime.cy = graph;
      const detach = pointer.attach();
      graph.on("viewport", publish);
      graph.on("scrollzoom pinchzoom", commands.navigate);
      return () => {
        detach();
        graph.off("viewport", publish);
        graph.off("scrollzoom pinchzoom", commands.navigate);
        runtime.cy = undefined;
      };
    },
    sync(elements: readonly GraphElement[]) {
      if (!latest.current.map) return;
      runtime.owners = moduleOwners(elements);
      const model = mapEvidence(
        mapModel(elements, getActiveTheme()),
        elements,
        stateStore.getState().state,
        latest.current.cone ?? latest.current.liveCone,
      );
      measureHeaders(model);
      runtime.frame = { ...runtime.frame, model, calls: mapCalls(elements) };
      publish();
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
