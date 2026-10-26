import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";

import { stateStore } from "../state";
import { getActiveTheme } from "../theme";

import { graphElements } from "./elements";
import { edgeShape, graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

export function GraphView() {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = container.current;
    if (!host) return;
    const theme = getActiveTheme();
    let cancelled = false;
    let dispose: (() => void) | undefined;
    const create = () => {
      const cy = cytoscape({ container: host, layout: { name: "preset" } });
      cy.style(
        graphStyles(
          theme,
          edgeShape(
            import.meta.env.DEV ? (typeof location === "undefined" ? "" : location.search) : "",
          ),
        ),
      );
      const sync = () => {
        updateGraph(cy, graphElements(stateStore.getState().state));
      };
      const unsubscribe = stateStore.subscribe(sync);
      sync();
      const resize = new ResizeObserver(() => cy.resize());
      resize.observe(host);
      dispose = () => {
        unsubscribe();
        resize.disconnect();
        cy.destroy();
      };
    };
    // Create labels only after the font settles: reapplying identical styles
    // cannot invalidate Cytoscape's cached label measurements and textures.
    void document.fonts
      .load(`${theme["body-size"]} ${theme["text-font"]}`)
      .catch(() => undefined)
      .then(() => {
        if (!cancelled) create();
      });
    return () => {
      cancelled = true;
      dispose?.();
    };
  }, []);
  return (
    <div
      ref={container}
      className="h-full w-full bg-ground"
      role="img"
      aria-label="Repository graph"
    />
  );
}
