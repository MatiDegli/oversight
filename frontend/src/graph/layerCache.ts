import type { NodeSingular } from "cytoscape";

import type { Layer } from "./layers";

/** Share all nine background properties, invalidating only when their inputs change. */
export function cacheLayers(compute: (node: NodeSingular) => Layer[], measured: boolean) {
  const cache = new WeakMap<NodeSingular, { inputs: string; layers: Layer[] }>();
  return (node: NodeSingular): Layer[] => {
    const inputs = JSON.stringify([
      node.data(),
      node.classes(),
      ...(measured ? [node.outerWidth(), node.outerHeight()] : []),
    ]);
    const previous = cache.get(node);
    if (previous?.inputs === inputs) return previous.layers;
    const layers = compute(node);
    cache.set(node, { inputs, layers });
    return layers;
  };
}
