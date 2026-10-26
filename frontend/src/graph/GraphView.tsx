import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";

import { stateStore } from "../state";
import * as theme from "../theme";

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
    const cy = cytoscape({ container: host, layout: { name: "preset" } });
    cy.style(graphStyles(theme));
    const sync = () => {
      updateGraph(cy, graphElements(stateStore.getState().state));
    };
    const unsubscribe = stateStore.subscribe(sync);
    sync();
    const resize = new ResizeObserver(() => cy.resize());
    resize.observe(host);
    void document.fonts.load(`14px ${theme.fontFamilies.sans}`).then(
      () => {
        if (cancelled) return;
        cy.style(graphStyles(theme));
        cy.resize();
      },
      () => {
        // Keep the graph usable with the fallback font when the asset fails.
      },
    );
    return () => {
      cancelled = true;
      unsubscribe();
      resize.disconnect();
      cy.destroy();
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
