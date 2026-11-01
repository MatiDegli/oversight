import { useSyncExternalStore } from "react";
import type { ComponentProps } from "react";
import { RoutingOmissions } from "./RoutingOmissions";
import type { MapController } from "./mapController";
import { agentLook } from "../theme";
import type { MapModel } from "./mapModel";
import { panelBox } from "./mapModel";
import type { MapLevel } from "./mapLevel";

const mapStyles = `
/* Map at rest: geometry follows the camera, type remains screen sized. */
.map-layer,
[aria-label="Repository graph"][data-map-level] {
  transition: opacity var(--transition-duration) var(--transition-easing);
}
.map-panel {
  border: 1px solid var(--node-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--card) 60%, transparent);
  box-shadow: var(--top-highlight);
}
.map-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--divider);
  line-height: 20px;
  color: var(--muted);
  font-size: 13px;
}
.map-header strong {
  font-size: 15px;
  font-weight: 600;
  color: var(--module-label);
}
.map-circle {
  background: var(--card);
  border: 1.5px solid var(--edge);
}
.map-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--module-label);
  line-height: 1.2;
}
.map-count,
.map-agent {
  font-size: 12px;
  line-height: 1.2;
}
.map-count {
  color: var(--muted);
}
.map-agent {
  font-weight: 600;
  position: relative;
  left: 50%;
  width: max-content;
  white-space: nowrap;
  transform: translateX(-50%);
}
[aria-label="Repository graph"][data-map-level="map"] {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .map-layer,
  [aria-label="Repository graph"][data-map-level] {
    transition: none;
  }
}
`.replaceAll("\n", " ");

export interface MapFrame {
  model: MapModel;
  zoom: number;
  pan: { x: number; y: number };
  level: MapLevel;
}

/** Screen-sized type and circles, world-sized cells. This layer never accepts input. */
export function MapLayer({ frame }: { frame: MapFrame }) {
  const { model, zoom, pan, level } = frame;
  const radius = Math.max(5, Math.min(9, Math.round(model.cellHeight * zoom * 0.1)));
  return (
    <div
      aria-hidden="true"
      className="map-layer pointer-events-none absolute inset-0 overflow-hidden"
      style={{ opacity: level === "map" ? 1 : 0 }}
    >
      <style>{mapStyles}</style>
      {model.groups.map((group) => {
        const box = panelBox(group, zoom, model.groups);
        return (
          <div key={group.id}>
            {group.panel && (
              <div
                data-map-group={group.id}
                className="map-panel absolute"
                style={{
                  left: box.x1 * zoom + pan.x,
                  top: box.y1 * zoom + pan.y,
                  width: (box.x2 - box.x1) * zoom,
                  height: (box.y2 - box.y1) * zoom,
                }}
              >
                <div className="map-header" style={{ height: (group.y - box.y1) * zoom }}>
                  <strong>{group.label}</strong>
                  <span>{group.modules.length} modules</span>
                </div>
              </div>
            )}
            {group.modules.map((module) => (
              <div
                key={module.id}
                className="absolute text-center"
                style={{
                  left: module.x * zoom + pan.x,
                  top: module.y * zoom + pan.y,
                  width: model.cellWidth * zoom - 10,
                  transform: "translate(-50%, -50%)",
                }}
              >
                <div className="relative mx-auto" style={{ width: radius * 2, height: radius * 2 }}>
                  <div data-map-circle className="map-circle absolute inset-0 rounded-full" />
                  {module.agents.map(({ slot }) => (
                    <div
                      key={slot}
                      className={`absolute rounded-full border-2 border-dashed ${agentLook[slot].border}`}
                      style={{ inset: -5 }}
                    />
                  ))}
                </div>
                <div className="map-name mt-[3px] truncate">{module.name}</div>
                <div className="map-count">
                  {module.functions
                    ? `${String(module.functions)} ${module.functions === 1 ? "function" : "functions"}`
                    : "empty"}
                </div>
                {module.agents.map(({ slot, text }) => (
                  <div key={slot} className={`map-agent ${agentLook[slot].textClass}`}>
                    {text}
                  </div>
                ))}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

/** Subscribe inside the opt-in layer, leaving the card graph's render lifecycle unchanged. */
export function ControlledMapLayer({ control }: { control: MapController }) {
  const frame = useSyncExternalStore(control.subscribe, control.getFrame, control.getFrame);
  return <MapLayer frame={frame} />;
}

export function MapRoutingOmissions({
  control,
  ...props
}: ComponentProps<typeof RoutingOmissions> & { control: MapController }) {
  const frame = useSyncExternalStore(control.subscribe, control.getFrame, control.getFrame);
  return (
    <div hidden={frame.level === "map"}>
      <RoutingOmissions {...props} />
    </div>
  );
}
