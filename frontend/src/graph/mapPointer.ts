import type { Core } from "cytoscape";
import type { RefObject } from "react";
import type { MapFrame } from "./MapLayer";
import { mapHit, type MapHover } from "./mapLines";

interface MapPointerOptions {
  host: RefObject<HTMLDivElement | null>;
  active: () => boolean | undefined;
  graph: () => Core | undefined;
  frame: () => MapFrame;
  setHover: (hit?: MapHover) => void;
  open: (hit: MapHover) => void;
  navigate: () => void;
}
type Point = { x: number; y: number };
interface PointerState {
  point?: Point;
  down?: Point;
  dragged: boolean;
}

/** The map owns its gestures before Cytoscape's canvas can blur the focused control. */
function mapGestures(options: MapPointerOptions, state: PointerState) {
  const { host, active } = options;
  const surface = (event: MouseEvent) =>
    host.current?.contains(event.target as Node) || event.target === host.current?.parentElement;
  const mousedown = (event: MouseEvent) => {
    if (!active() || !surface(event) || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    state.down = localPoint(host, event);
    state.dragged = false;
  };
  const mousemove = (event: MouseEvent) => {
    if (!active()) return;
    if (surface(event)) event.stopPropagation();
    const point = localPoint(host, event),
      cy = options.graph();
    if (!state.down || !point || !cy) return;
    const dx = point.x - state.down.x,
      dy = point.y - state.down.y;
    if (Math.hypot(dx, dy) <= 3 && !state.dragged) return;
    state.dragged = true;
    cy.panBy({ x: dx, y: dy });
    state.down = point;
  };
  const mouseup = () => {
    if (!active()) return;
    state.down = undefined;
  };
  const click = (event: MouseEvent) => {
    if (!active() || !options.graph() || !surface(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (state.dragged) return;
    const point = localPoint(host, event),
      hit = point && mapHit(point, options.frame());
    if (hit) options.open(hit);
  };
  const wheel = (event: WheelEvent) => {
    const cy = options.graph();
    if (!active() || !cy || host.current?.contains(event.target as Node)) return;
    event.preventDefault();
    const renderedPosition = localPoint(host, event);
    if (!renderedPosition) return;
    cy.zoom({
      level: Math.min(
        cy.maxZoom(),
        Math.max(cy.minZoom(), cy.zoom() * Math.pow(10, -event.deltaY / 250)),
      ),
      renderedPosition,
    });
    options.navigate();
  };
  return { mousedown, mousemove, mouseup, click, wheel };
}

function localPoint(host: MapPointerOptions["host"], event: MouseEvent) {
  const box = host.current?.getBoundingClientRect();
  return box ? { x: event.clientX - box.left, y: event.clientY - box.top } : undefined;
}

function attachMapPointer(
  stage: HTMLElement | null | undefined,
  gestures: ReturnType<typeof mapGestures>,
  pointermove: (event: PointerEvent) => void,
  pointerleave: () => void,
) {
  const scope = stage?.ownerDocument.defaultView;
  const controller = new AbortController();
  const capture = { capture: true, signal: controller.signal };
  const passive = { passive: false, signal: controller.signal };
  stage?.addEventListener("mousedown", gestures.mousedown, capture);
  scope?.addEventListener("mousemove", gestures.mousemove, capture);
  stage?.addEventListener("click", gestures.click, capture);
  scope?.addEventListener("mouseup", gestures.mouseup, passive);
  stage?.addEventListener("pointermove", pointermove, passive);
  stage?.addEventListener("pointerleave", pointerleave, passive);
  stage?.addEventListener("wheel", gestures.wheel, passive);
  return () => {
    controller.abort();
  };
}

/** Pointer and viewport updates share one hit test per animation frame. */
export function createMapPointer(options: MapPointerOptions) {
  const state: PointerState = { dragged: false };
  let animation = 0;
  const refresh = () => {
    if (!options.active() || animation) return;
    animation = requestAnimationFrame(() => {
      animation = 0;
      if (options.active()) options.setHover(state.point && mapHit(state.point, options.frame()));
    });
  };
  const pointermove = (event: PointerEvent) => {
    if (!options.active()) return;
    state.point = localPoint(options.host, event);
    refresh();
  };
  const clear = () => {
    cancelAnimationFrame(animation);
    animation = 0;
    state.point = undefined;
    options.setHover();
  };
  const pointerleave = () => {
    state.point = undefined;
    if (options.active()) clear();
  };
  return {
    refresh,
    attach() {
      const detach = attachMapPointer(
        options.host.current?.parentElement,
        mapGestures(options, state),
        pointermove,
        pointerleave,
      );
      return () => {
        detach();
        clear();
        state.down = undefined;
      };
    },
  };
}
