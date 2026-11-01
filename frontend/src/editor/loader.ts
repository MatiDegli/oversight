import { whenFirstGraphDrawn } from "../graph/firstDraw";

let pending: Promise<typeof import("./implementation")> | undefined;
export type EditorImplementation = typeof import("./implementation");
let loaded: EditorImplementation | undefined;

export const loadedEditor = () => loaded;

/** Explicit opens and idle prefetch share both the graph gate and the evaluated module. */
export function loadEditor() {
  return (pending ??= whenFirstGraphDrawn()
    .then(() => import("./implementation"))
    .then((module) => {
      loaded = module;
      return module;
    }));
}
