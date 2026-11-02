import { useSyncExternalStore } from "react";
import type { ComponentProps } from "react";
import { RoutingOmissions } from "./RoutingOmissions";
import type { MapController } from "./mapController";
import { agentLook } from "../theme";
import type { MapModel, MapGroup, MapModule as Module } from "./mapModel";
import { MapLines } from "./MapLines";
import { decisionsLabel, testedLabel } from "./mapEvidence";
import { moduleLayout, panelBox } from "./mapModel";
import { distanceBadge, pointed } from "./mapLinesModel";
import type { MapCall, MapHover } from "./mapLinesModel";
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
  gap: 3px 10px;
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
.map-header > * { flex-shrink: 0; white-space: nowrap; }
.map-rings { display: flex; gap: 4px; }
.map-ring { width: 20px; height: 20px; border-width: 2px; border-style: solid; border-radius: 50%; font-size: 10.5px; font-weight: 700; display: grid; place-items: center; }
.map-decision { height: 22px; margin-block: -1px; padding: 0 8px; border: 1px solid var(--border); border-radius: 11px; font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 6px; color: var(--text); }
.map-decision::before { content: ""; width: 7px; height: 7px; background: var(--text); border-radius: 50%; }
.map-header b { color: var(--text); font-weight: 600; }
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
.map-distance { position: absolute; width: 20px; height: 20px; transform: translate(-50%, -50%); border-radius: 50%; background: var(--ground); color: var(--text); font-size: 12px; font-weight: 700; display: grid; place-items: center; }
.map-full-name { position: relative; left: 50%; width: max-content; transform: translateX(-50%); overflow: visible; white-space: nowrap; }
.map-count,
.map-agent {
  font-size: 12px;
  line-height: 1.2;
}
.map-count {
  color: var(--muted);
}
/* Match the unlit surface; expanded hover labels retain their existing backing. */
.map-count, .map-agent:not(.map-agent-full) {
  background: var(--ground);
}
.map-panel ~ [data-map-module] :is(.map-count, .map-agent:not(.map-agent-full)) {
  background: color-mix(in srgb, var(--card) 60%, var(--ground));
}
.map-agent {
  font-weight: 600;
  position: relative;
  left: 50%;
  width: max-content;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transform: translateX(-50%);
}
.map-agent-full {
  max-width: none;
  overflow: visible;
  text-overflow: clip;
  background: color-mix(in srgb, var(--card) 85%, transparent);
}
[aria-label="Repository graph"][data-map-level="map"] {
  opacity: 0;
}
[data-map-level="map"] [data-field-markers] { visibility: hidden; }
@media (max-width: 1439px) { .map-legend-hint { display: none; } }
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
  hover?: MapHover;
  calls?: MapCall[];
}

/** Screen-sized type and circles, world-sized cells. This layer never accepts input. */
export function MapLayer({ frame }: { frame: MapFrame }) {
  const { model, zoom, pan, level } = frame;
  return (
    <div
      aria-hidden="true"
      className="map-layer pointer-events-none absolute inset-0 overflow-hidden"
      style={{ opacity: level === "map" ? 1 : 0 }}
    >
      <style>{mapStyles}</style>
      <MapLines frame={frame} />
      {model.groups.map((group) => (
        <div key={group.id}>
          {group.panel && <MapPanel group={group} frame={frame} />}
          {group.modules.map((module) => {
            const layout = moduleLayout(module, model, zoom, pan);
            const full = pointed(module.id, group.id, frame.hover);
            const dim = module.inCone === false && !module.agents.length;
            const hidden = dim && !full ? "hidden" : undefined;
            return (
              <div
                key={module.id}
                data-map-module={module.id}
                className="absolute text-center"
                style={{
                  zIndex: 2,
                  left: layout.x,
                  top: layout.y1,
                  width: model.cellWidth * zoom - 10,
                  transform: "translateX(-50%)",
                }}
              >
                <MapModuleCircle module={module} radius={layout.radius} dim={dim} />
                <div
                  className={`map-name mt-[3px] ${full ? "map-full-name" : "truncate"}`}
                  style={{
                    visibility: hidden,
                    background:
                      frame.hover || module.inCone !== undefined
                        ? "color-mix(in srgb, var(--card) 85%, transparent)"
                        : undefined,
                  }}
                >
                  {full ? (module.fullName ?? module.name) : module.name}
                </div>
                <div className="map-count" style={{ visibility: hidden }}>
                  {module.functions
                    ? `${String(module.functions)} ${module.functions === 1 ? "function" : "functions"}`
                    : "empty"}
                </div>
                {module.agents.map(({ slot, text }) => (
                  <div
                    key={slot}
                    className={`map-agent ${full ? "map-agent-full " : ""}${agentLook[slot].textClass}`}
                  >
                    {text}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function MapPanel({ group, frame }: { group: MapGroup; frame: MapFrame }) {
  const { model, zoom, pan } = frame;
  const box = panelBox(group, zoom, model.groups);
  return (
    <div
      data-map-group={group.id}
      className="map-panel absolute"
      style={{
        opacity: group.modules.every((m) => m.inCone === false) ? 0.5 : 1,
        borderColor:
          (frame.hover?.kind === "group" && frame.hover.id === group.id) ||
          (frame.hover?.kind === "module" && group.modules.some((m) => m.id === frame.hover?.id))
            ? "var(--edge)"
            : undefined,
        left: box.x1 * zoom + pan.x,
        top: box.y1 * zoom + pan.y,
        width: (box.x2 - box.x1) * zoom,
        height: (box.y2 - box.y1) * zoom,
      }}
    >
      <div className="map-header" style={{ height: (group.y - box.y1) * zoom }}>
        <strong>{group.label}</strong>
        <span>{group.modules.length} modules</span>
        {!!group.rings?.length && (
          <span className="map-rings">
            {group.rings.map((slot) => (
              <span
                key={slot}
                className={`map-ring ${agentLook[slot].border} ${agentLook[slot].textClass}`}
              >
                {slot}
              </span>
            ))}
          </span>
        )}
        {!!group.decisions && (
          <span className="map-decision">{decisionsLabel(group.decisions)}</span>
        )}
        {group.coneCount ? (
          <span>
            <b>
              {group.coneCount} of {group.coneTotal}
            </b>{" "}
            in {group.label}
          </span>
        ) : (
          testedLabel(group.tested ?? 0, group.id === "tests") && (
            <span>{testedLabel(group.tested ?? 0, group.id === "tests")}</span>
          )
        )}
      </div>
    </div>
  );
}

type CircleProps = { module: Module; radius: number; dim: boolean };
function MapModuleCircle({ module, radius, dim }: CircleProps) {
  const badge = distanceBadge(module);
  return (
    <div className="relative mx-auto" style={{ width: radius * 2, height: radius * 2 }}>
      <div
        data-map-circle
        className="map-circle absolute inset-0 rounded-full"
        style={{ opacity: dim ? "var(--dim-node)" : 1 }}
      />
      {badge !== undefined && (
        <div
          data-map-distance
          className="map-distance"
          style={{
            left: radius + 0.75 * radius + 4,
            top: radius - 0.75 * radius - 4,
          }}
        >
          {badge}
        </div>
      )}
      {module.agents.map(({ slot }) => (
        <div
          key={slot}
          className={`absolute rounded-full border-2 border-dashed ${agentLook[slot].border}`}
          style={{ inset: -5 }}
        />
      ))}
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
