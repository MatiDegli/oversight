import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";

import { stateStore } from "../state";

import { graphElements } from "./elements";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

export function GraphView() {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = container.current;
    if (!host) return;
    let cancelled = false;
    let dispose = () => {};
    void document.fonts.ready.then(() => {
      if (cancelled) return;
      const cy = cytoscape({ container: host, style: graphStyles, layout: { name: "preset" } });
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
    });
    return () => {
      cancelled = true;
      dispose();
    };
  }, []);
  return (
    <div
      ref={container}
      className="h-full w-full bg-background"
      role="img"
      aria-label="Repository graph"
    />
  );
}
