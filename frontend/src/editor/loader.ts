// eslint-disable-next-line no-restricted-imports -- C1 uses a pure leaf to isolate graph startup.
import { whenFirstGraphDrawn } from "../graph/firstDraw";

let pending: Promise<typeof import("./implementation")> | undefined;
export type EditorImplementation = typeof import("./implementation");
let loaded: EditorImplementation | undefined;
/** A committed Chat-first shell has no graph stage to wait for. Eligibility imports nothing. */
export let allowEditorWithoutGraph: () => void;
export const editorReady = new Promise<void>((resolve) => {
  allowEditorWithoutGraph = resolve;
  void whenFirstGraphDrawn().then(resolve);
});

export const loadedEditor = () => loaded;

/** Explicit opens and idle prefetch share eligibility and the evaluated module. */
export function loadEditor() {
  return (pending ??= editorReady.then(async () => {
    return (loaded = await import("./implementation"));
  }));
}
