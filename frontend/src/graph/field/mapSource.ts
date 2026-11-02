import type { MapController } from "../mapController";
import { moduleLayout } from "../mapModel";
import type { BoxSource } from "./boxSource";
import type { Box } from "./model";

type MapSourceControl = Pick<MapController, "getFrame" | "moduleOwners" | "subscribe" | "level">;

/** Only emitters use map cells; contour frames, routes and masks retain their card sources. */
export function createMapSource(cards: BoxSource, control: MapSourceControl): BoxSource {
  const listeners = new Set<() => void>();
  const notify = () => {
    for (const listener of [...listeners]) listener();
  };
  let stopCards: (() => void) | undefined;
  let stopMap: (() => void) | undefined;
  let attached = false;
  return {
    ...cards,
    level: () => (attached ? control.level() : "cards"),
    attach(cy) {
      stopCards?.();
      stopMap?.();
      attached = true;
      stopCards = cards.subscribe(notify);
      stopMap = control.subscribe(notify);
      cards.attach(cy);
    },
    detach() {
      attached = false;
      stopCards?.();
      stopMap?.();
      stopCards = stopMap = undefined;
      cards.detach();
      notify();
    },
    boxes(ids) {
      const { model, zoom, pan } = control.getFrame();
      if (control.level() !== "map") return cards.boxes(ids);
      const found = new Map<string, Box>();
      if (!attached) return found;
      const modules = new Map(model.groups.flatMap((g) => g.modules.map((m) => [m.id, m])));
      const owners = control.moduleOwners();
      for (const id of ids) {
        const module = modules.get(owners.get(id) ?? "");
        if (!module) continue;
        const { x1, y1, x2, y2 } = moduleLayout(module, model, zoom, pan);
        found.set(id, { x1, y1, x2, y2 });
      }
      return found;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
