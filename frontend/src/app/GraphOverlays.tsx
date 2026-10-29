import { agentLook } from "../theme";
import type { RefObject } from "react";
import { Maximize, Minus, Plus } from "lucide-react";

import type { GraphApi } from "../graph";
import { selectGraphSummary } from "../state";
import type { State } from "../state";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
// M8 reserves frost for the graph's legend, zoom and future segmented controls.
const surface = "frosted border border-border";

const plural = (count: number, one: string, many: string) => (count === 1 ? one : many);

/** The repository's name, what is drawn of it and how many agents are at work. */
export function GraphHeading({ state }: { state: State }) {
  const { repo, modules, functions, working } = selectGraphSummary(state);
  if (repo === null) return null;
  const count = (value: number, one: string, many: string) =>
    `${String(value)} ${plural(value, one, many)}`;
  return (
    <div className="overlay-fill flex flex-wrap items-baseline gap-x-3.5 gap-y-1 rounded-control px-(--spacing-sm) py-(--spacing-xs)">
      <h2 className="text-(length:--headline-size) leading-(--headline-line-height) font-bold">
        {repo}
      </h2>
      <p className="flex flex-wrap gap-x-3.5 text-sm text-muted">
        <span>{count(modules, "module", "modules")}</span>
        <span>{count(functions, "function", "functions")}</span>
        <span>{count(working, "agent working", "agents working")}</span>
      </p>
    </div>
  );
}

/**
 * What the graph's looks mean, in the states it can draw now. A glyph is never the only signal: each
 * has its words. A dashed border is the pending change, the one thing it means: filled for a node
 * an agent is editing or has changed, an outline for one that main does not have yet. (The sketch
 * draws editing solid; DESIGN.md's departures table has the dashed border.)
 */
export function Legend({ state }: { state: State }) {
  const slots = state.run?.slots ?? [];
  if (!state.run) return null;
  return (
    <ul
      aria-label="Legend"
      className={`flex flex-wrap items-center gap-x-4.5 gap-y-1.5 rounded-module px-3.5 py-2.5 text-sm text-muted ${surface}`}
    >
      {slots.map(({ slot }) => (
        <li key={slot} className="flex items-center gap-2">
          <span aria-hidden="true" className={`size-3 rounded-label ${agentLook[slot].fill}`} />
          Agent {slot}
        </li>
      ))}
      <li className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-3.5 w-6 rounded-node border-(length:--agent-border-width) border-muted"
        />
        Read
      </li>
      <li className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-3.5 w-6 rounded-node border-(length:--agent-border-width) border-dashed border-muted bg-raised"
        />
        Editing or changed
      </li>
      <li className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-3.5 w-6 rounded-node border-(length:--agent-border-width) border-dashed border-muted"
        />
        Not in main yet
      </li>
      <li className="flex items-center gap-2">
        <span aria-hidden="true" className="w-6 border-t border-edge-possible" />
        Possible call
      </li>
    </ul>
  );
}

const zoomButton = `grid size-(--button-small-height) place-items-center text-text hover:bg-raised ${focusRing}`;

/** Zoom in, zoom out and fit. They move the camera and never a node. */
export function ZoomControls({ api }: { api: RefObject<GraphApi | null> }) {
  const buttons = [
    ["Zoom in", Plus, () => api.current?.zoomIn()],
    ["Zoom out", Minus, () => api.current?.zoomOut()],
    ["Fit the graph to the view", Maximize, () => api.current?.fit()],
  ] as const;
  return (
    <div
      role="group"
      aria-label="Zoom"
      className={`flex flex-col overflow-hidden rounded-card ${surface}`}
    >
      {buttons.map(([label, Icon, act]) => (
        <button
          key={label}
          type="button"
          aria-label={label}
          title={label}
          onClick={act}
          className={zoomButton}
        >
          <Icon aria-hidden="true" className="size-5" />
        </button>
      ))}
    </div>
  );
}

/** Presentation choices keep the preset; they never run a layout. */
export function DetailControls({
  level,
  zoom,
  onChange,
}: {
  level: "Modules" | "Functions" | "Details";
  zoom: number;
  onChange: (level: "Modules" | "Functions" | "Details") => void;
}) {
  return (
    <div
      className={`flex h-(--button-small-height) overflow-hidden rounded-card text-sm ${surface}`}
    >
      <div role="group" aria-label="Graph detail" className="flex">
        {(["Modules", "Functions", "Details"] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={level === value}
            onClick={() => {
              onChange(value);
            }}
            className={`px-3.5 text-muted hover:bg-raised aria-pressed:bg-raised aria-pressed:text-text ${focusRing}`}
          >
            {value}
          </button>
        ))}
      </div>
      <output
        aria-label="Zoom percentage"
        className="flex min-w-16 items-center justify-center border-l border-border px-3 font-mono text-muted"
      >
        {zoom}%
      </output>
    </div>
  );
}
