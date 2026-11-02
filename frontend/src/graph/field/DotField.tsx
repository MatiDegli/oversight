import { useEffect, useRef } from "react";
import type { RefObject } from "react";

import type { BoxSource } from "./boxSource";
import type { FieldController, FieldHost, FieldInput } from "./controller";
import { loadFieldController } from "./controllerLoader";

let createController: typeof import("./controller").createFieldController | undefined;
let preparation: Promise<void> | undefined;

/** The stage awaits optional code before mounting; standalone fields handle failure too. */
export function prepareDotField(): Promise<void> {
  return (preparation ??= loadFieldController()
    .then((module) => {
      createController = module.createFieldController;
    })
    .catch(() => {
      preparation = undefined;
    }));
}

export interface DotFieldProps extends FieldInput {
  /** Where the lit nodes are drawn, and when that may change: the graph's side of the field. */
  source: RefObject<BoxSource>;
}

/**
 * The dot field: its own canvas under the transparent Cytoscape canvas, 7px lamps fixed to the
 * screen that light up around the nodes in `lights`. It never takes the pointer, the focus or a
 * place in the reading order, and without WebGL it draws nothing.
 */
export function DotField({
  source,
  mode,
  lights,
  merges,
  modules,
  milestones,
  mergeStarts,
}: DotFieldProps) {
  const canvas = useRef<FieldHost>(null);
  const controller = useRef<FieldController | null>(null);
  const input = useRef<FieldInput>({ mode, lights, merges, modules, milestones, mergeStarts });
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const host = element;
    const initial = input.current;
    let cancelled = false;
    let resize: ResizeObserver | undefined;
    function mount() {
      if (cancelled || !createController) return;
      const created = createController(host, source.current);
      created.update(initial);
      created.update(input.current);
      controller.current = created;
      resize = new ResizeObserver(() => {
        created.redraw();
      });
      resize.observe(host);
    }
    if (createController) mount();
    else
      void prepareDotField()
        .then(mount)
        .catch(() => undefined);
    return () => {
      cancelled = true;
      resize?.disconnect();
      controller.current?.dispose();
      controller.current = null;
    };
  }, [source]);
  useEffect(() => {
    input.current = { mode, lights, merges, modules, milestones, mergeStarts };
    controller.current?.update(input.current);
  }, [mode, lights, merges, modules, milestones, mergeStarts]);
  return (
    <canvas
      ref={canvas}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 size-full"
    />
  );
}
