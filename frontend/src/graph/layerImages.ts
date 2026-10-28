import type { Core, EventObject, NodeSingular } from "cytoscape";

const prefix = "data:image/svg+xml,";
const sources = new Map<string, string>();

/** Resolve short image URLs for SVG assertions without changing the image bytes. */
export function imageSource(url: string): string {
  return sources.get(url) ?? url;
}

/** One URL per distinct SVG, owned by this stylesheet and by the core using it. */
export function layerImages() {
  const pools = new WeakMap<Core, Map<string, string>>();
  return (node: NodeSingular, image: string): string => {
    if (typeof URL.createObjectURL !== "function" || !image.startsWith(prefix)) return image;
    const cy = node.cy();
    let urls = pools.get(cy);
    if (!urls) {
      urls = new Map<string, string>();
      pools.set(cy, urls);
      const owned = urls;
      const style = cy.style();
      const release = (event: EventObject) => {
        if (event.type !== "destroy" && cy.style() === style && cy.elements().nonempty()) return;
        for (const url of owned.values()) {
          URL.revokeObjectURL(url);
          sources.delete(url);
        }
        owned.clear();
        pools.delete(cy);
        cy.off("style remove destroy", release);
      };
      // Style events bubble from elements. Also release when the final element is removed, so
      // replacing the style on an empty graph cannot leave its old images alive.
      cy.on("style remove destroy", release);
    }
    let url = urls.get(image);
    if (url === undefined) {
      const svg = decodeURIComponent(image.slice(prefix.length));
      url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
      urls.set(image, url);
      sources.set(url, image);
    }
    return url;
  };
}
