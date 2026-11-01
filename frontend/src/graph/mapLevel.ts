export type MapLevel = "cards" | "map";
export const MAP_MIN_MODULES = 24;
export const MAP_ENTER = 11 / 17;
export const MAP_LEAVE = 11 / 16;

export function nextLevel(current: MapLevel, zoom: number, shownModules: number): MapLevel {
  if (shownModules < MAP_MIN_MODULES) return "cards";
  return zoom < MAP_ENTER ? "map" : zoom > MAP_LEAVE ? "cards" : current;
}

/** Explicit framing chooses from its target, including cards at the start camera's exact floor. */
export function targetLevel(zoom: number, shownModules: number): MapLevel {
  return shownModules >= MAP_MIN_MODULES && zoom < MAP_ENTER ? "map" : "cards";
}
