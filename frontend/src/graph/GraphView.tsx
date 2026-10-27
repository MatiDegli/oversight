import cytoscape from "cytoscape";
import type { Core, NodeSingular } from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";

import { stateStore } from "../state";
import { getActiveTheme } from "../theme";

import { frameCamera, prefersReducedMotion } from "./camera";
import { graphElements } from "./elements";
import { exposeProbe } from "./probe";
import { showSelection } from "./selection";
import { edgeShape, graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

export interface GraphViewProps {
  /** The node with the selected ring. The app owns it; taps only report. */
  selectedId?: string | null;
  onNodeTap?: (nodeId: string) => void;
  /**
   * Whether the left panel is open. The graph's width changes with it, so when this flips the
   * camera moves (to the selected node's neighbourhood on open, to the whole graph on close).
   */
  panelOpen?: boolean;
  /** The nodes that stay lit while everything else dims, as in review; null dims nothing. */
  litIds?: readonly string[] | null;
}

export function GraphView({
  selectedId = null,
  onNodeTap,
  panelOpen = false,
  litIds = null,
}: GraphViewProps = {}) {
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<Core | undefined>(undefined);
  const latest = useRef<GraphViewProps>({});
  const resync = useRef<(() => void) | undefined>(undefined);
  const framedFor = useRef(panelOpen);
  useEffect(() => {
    const relit = latest.current.litIds !== litIds;
    latest.current = { selectedId, onNodeTap, panelOpen, litIds };
    if (relit) resync.current?.();
    else if (graph.current) showSelection(graph.current, selectedId);
  }, [selectedId, onNodeTap, panelOpen, litIds]);
  // Only a change of the panel moves the camera, never a tap or an event; the first render keeps
  // the fit the first layout made.
  useEffect(() => {
    if (framedFor.current === panelOpen) return;
    framedFor.current = panelOpen;
    if (graph.current) {
      frameCamera(
        graph.current,
        panelOpen ? (latest.current.selectedId ?? null) : null,
        prefersReducedMotion(),
      );
    }
  }, [panelOpen]);
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
      cy.style(
        graphStyles(
          theme,
          edgeShape(
            import.meta.env.DEV ? (typeof location === "undefined" ? "" : location.search) : "",
          ),
        ),
      );
      const sync = () => {
        const lit = latest.current.litIds;
        updateGraph(
          cy,
          graphElements(stateStore.getState().state, false, lit ? new Set(lit) : null),
        );
        showSelection(cy, latest.current.selectedId ?? null);
      };
      resync.current = sync;
      cy.on("tap", "node", (event) => {
        latest.current.onNodeTap?.((event.target as NodeSingular).id());
      });
      graph.current = cy;
      const unexpose = exposeProbe(host, cy);
      const unsubscribe = stateStore.subscribe(sync);
      sync();
      const resize = new ResizeObserver(() => cy.resize());
      resize.observe(host);
      dispose = () => {
        unsubscribe();
        unexpose();
        resize.disconnect();
        resync.current = undefined;
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
