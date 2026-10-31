export type MapLevel = "cards" | "map";
export const MAP_ENTER = 11 / 17;
export const MAP_LEAVE = 11 / 16;

export function nextLevel(current: MapLevel, zoom: number): MapLevel {
  return zoom < MAP_ENTER ? "map" : zoom > MAP_LEAVE ? "cards" : current;
}

/** Explicit framing chooses from its target, including cards at the start camera's exact floor. */
export function targetLevel(zoom: number): MapLevel {
  return zoom < MAP_ENTER ? "map" : "cards";
}
