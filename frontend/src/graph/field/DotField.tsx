import { useEffect, useRef } from "react";
import type { RefObject } from "react";

import type { BoxSource } from "./boxSource";
import { createFieldController } from "./controller";
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
export function DotField({ source, mode, lights }: DotFieldProps) {
  const canvas = useRef<FieldHost>(null);
  const controller = useRef<FieldController | null>(null);
  const input = useRef<FieldInput>({ mode, lights });
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const created = createFieldController(element, source.current);
    created.update(input.current);
    controller.current = created;
    const resize = new ResizeObserver(() => {
      created.redraw();
    });
    resize.observe(element);
    return () => {
      resize.disconnect();
      created.dispose();
      controller.current = null;
    };
  }, [source]);
  useEffect(() => {
    input.current = { mode, lights };
    controller.current?.update(input.current);
  }, [mode, lights]);
  return (
    <canvas
      ref={canvas}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 size-full"
    />
  );
}
