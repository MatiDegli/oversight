import cytoscape from "cytoscape";
import type { Core, NodeSingular } from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";

import { stateStore } from "../state";
import { getActiveTheme } from "../theme";

import { graphElements } from "./elements";
import { showSelection } from "./selection";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

export interface GraphViewProps {
  /** The node with the selected ring. The app owns it; taps only report. */
  selectedId?: string | null;
  onNodeTap?: (nodeId: string) => void;
}

export function GraphView({ selectedId = null, onNodeTap }: GraphViewProps = {}) {
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<Core | undefined>(undefined);
  const latest = useRef<GraphViewProps>({});
  useEffect(() => {
    latest.current = { selectedId, onNodeTap };
    if (graph.current) showSelection(graph.current, selectedId);
  }, [selectedId, onNodeTap]);
  useEffect(() => {
    const host = container.current;
    if (!host) return;
    const theme = getActiveTheme();
    let cancelled = false;
    let dispose: (() => void) | undefined;
    const create = () => {
      const cy = cytoscape({
        container: host,
        layout: { name: "preset" },
        boxSelectionEnabled: false,
      });
      cy.style(graphStyles(theme));
      const sync = () => {
        updateGraph(cy, graphElements(stateStore.getState().state));
        showSelection(cy, latest.current.selectedId ?? null);
      };
      cy.on("tap", "node", (event) => {
        latest.current.onNodeTap?.((event.target as NodeSingular).id());
      });
      graph.current = cy;
      const unsubscribe = stateStore.subscribe(sync);
      sync();
      const resize = new ResizeObserver(() => cy.resize());
      resize.observe(host);
      dispose = () => {
        unsubscribe();
        resize.disconnect();
        graph.current = undefined;
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
