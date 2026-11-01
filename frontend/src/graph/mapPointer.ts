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
    state.down = state.point ?? localPoint(host, event);
    state.dragged = false;
  };
  const mousemove = (event: MouseEvent) => {
    if (active() && surface(event)) event.stopPropagation();
  };
  const mouseup = () => {
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
  stage?.addEventListener("mousedown", gestures.mousedown, true);
  stage?.addEventListener("mousemove", gestures.mousemove, true);
  stage?.addEventListener("click", gestures.click, true);
  stage?.ownerDocument.defaultView?.addEventListener("mouseup", gestures.mouseup);
  stage?.addEventListener("pointermove", pointermove);
  stage?.addEventListener("pointerleave", pointerleave);
  stage?.addEventListener("wheel", gestures.wheel, { passive: false });
  return () => {
    stage?.removeEventListener("mousedown", gestures.mousedown, true);
    stage?.removeEventListener("mousemove", gestures.mousemove, true);
    stage?.removeEventListener("click", gestures.click, true);
    stage?.ownerDocument.defaultView?.removeEventListener("mouseup", gestures.mouseup);
    stage?.removeEventListener("pointermove", pointermove);
    stage?.removeEventListener("pointerleave", pointerleave);
    stage?.removeEventListener("wheel", gestures.wheel);
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
    const cy = options.graph();
    if (state.down && state.point && cy) {
      const dx = state.point.x - state.down.x,
        dy = state.point.y - state.down.y;
      if (Math.hypot(dx, dy) > 3 || state.dragged) {
        state.dragged = true;
        cy.panBy({ x: dx, y: dy });
        state.down = state.point;
      }
    }
    refresh();
  };
  const clear = () => {
    cancelAnimationFrame(animation);
    animation = 0;
    state.point = undefined;
    options.setHover();
  };
  const pointerleave = () => {
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
