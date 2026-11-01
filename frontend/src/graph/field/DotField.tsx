import { useEffect, useRef } from "react";
import type { RefObject } from "react";

import type { BoxSource } from "./boxSource";
import type { FieldController, FieldHost, FieldInput } from "./controller";

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
    // Load the field separately so the graph and controls stay within the startup budget.
    const initial = input.current;
    let cancelled = false;
    let resize: ResizeObserver | undefined;
    void import("./controller").then(({ createFieldController }) => {
      if (cancelled) return;
      const created = createFieldController(element, source.current);
      created.update(initial);
      created.update(input.current);
      controller.current = created;
      resize = new ResizeObserver(() => {
        created.redraw();
      });
      resize.observe(element);
    });
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
