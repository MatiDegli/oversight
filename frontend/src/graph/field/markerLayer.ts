import { slotForFamily } from "../../theme";
import { getActiveTheme } from "../../theme";
import { unmaskedPath } from "./mask";
import { lightBoxKey } from "./marker";
import type { FieldLight } from "./lights";
import type { Box } from "./model";

const ns = "http://www.w3.org/2000/svg";
let nextId = 0;
const attr = (element: Element, key: string, value: string) => {
  if (element.getAttribute(key) !== value) element.setAttribute(key, value);
};

/** The letter is above graph paint; its box-shaped emitter remains on the field canvas below. */
export class MarkerLayer {
  private readonly svg?: SVGSVGElement;
  private readonly clip?: SVGPathElement;
  private readonly items = new Map<string, SVGGElement>();

  constructor(canvas: HTMLCanvasElement) {
    if (!canvas.parentElement) return;
    this.svg = document.createElementNS(ns, "svg");
    this.svg.setAttribute("aria-hidden", "true");
    this.svg.setAttribute("data-field-markers", "");
    this.svg.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:1";
    const defs = document.createElementNS(ns, "defs");
    const clip = document.createElementNS(ns, "clipPath");
    const id = `field-markers-${String(++nextId)}`;
    clip.id = id;
    this.clip = document.createElementNS(ns, "path");
    clip.append(this.clip);
    defs.append(clip);
    this.svg.append(defs);
    this.svg.setAttribute("clip-path", `url(#${id})`);
    canvas.parentElement.append(this.svg);
  }

  draw(
    lights: readonly FieldLight[],
    boxes: ReadonlyMap<string, Box>,
    masks: readonly Box[],
    width: number,
    height: number,
  ) {
    if (!this.svg || !this.clip || (!lights.some((light) => light.marker) && this.items.size === 0))
      return;
    const theme = getActiveTheme();
    attr(this.clip, "d", unmaskedPath(masks, width, height));
    const shown = new Set<string>();
    for (const light of lights) {
      if (!light.marker) continue;
      const box = boxes.get(lightBoxKey(light));
      if (!box) continue;
      shown.add(light.marker);
      let group = this.items.get(light.marker);
      if (!group) {
        group = document.createElementNS(ns, "g");
        group.append(document.createElementNS(ns, "circle"), document.createElementNS(ns, "text"));
        this.svg.append(group);
        this.items.set(light.marker, group);
      }
      const circle = group.children[0];
      const text = group.children[1];
      if (!circle || !text) continue;
      attr(group, "transform", `translate(${String(box.x1)} ${String((box.y1 + box.y2) / 2)})`);
      attr(group, "opacity", String(light.weight / theme["watch-editing-weight"]));
      attr(circle, "r", String(Number.parseFloat(theme["letter-badge-size"]) / 2));
      attr(circle, "fill", theme[light.source]);
      attr(text, "fill", theme["on-agent"]);
      attr(text, "font-family", theme["text-font"]);
      attr(text, "font-size", theme["letter-badge-font-size"]);
      attr(text, "font-weight", String(theme["badge-weight"]));
      attr(text, "text-anchor", "middle");
      attr(text, "dominant-baseline", "central");
      const letter = light.source === "work-light" ? "" : slotForFamily(light.source);
      if (text.textContent !== letter) text.textContent = letter;
    }
    for (const [id, group] of this.items)
      if (!shown.has(id)) {
        group.remove();
        this.items.delete(id);
      }
  }

  dispose() {
    this.svg?.remove();
  }
}
