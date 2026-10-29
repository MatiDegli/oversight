import type { AgentFamily } from "../../theme";
import type { ImpactCone, Slot } from "@contracts";

/** The field's four modes: watch, the live diff, the file view and review. */
export type FieldMode = "watch" | "live-diff" | "file" | "review";

/** What the person has open, as far as the field cares. The app derives it from its view. */
export type FieldScope =
  | { mode: "watch" }
  | { mode: "live-diff"; changeId: string; cone?: ImpactCone | null }
  | { mode: "review"; changeId: string }
  | { mode: "file"; nodeId: string | null };

/** The theme token a light takes its color from: an agent's color, or the cone's neutral one. */
export type LightSource = AgentFamily | "work-light";

export interface FieldLight {
  /** R4 current focus, keyed by agent so its light travels instead of crossfading two pools. */
  marker?: string;
  /** Work rings do not modulate ambient light. Legacy scenes retain their old response. */
  steady?: boolean;
  id: string;
  source: LightSource;
  /** Before the mode's gain: 1 is the strongest light the mode draws. */
  weight: number;
  /** Sends working rings; R4 steady lights do not breathe. */
  pulses: boolean;
  /** Rendering envelope during an eased transition; pure selectors leave this unset. */
  pulseWeight?: number;
}

/** A merged change: where its pulse starts, where it arrives, and how long ago it happened. */
export interface FieldMerge {
  /** One merge, stable across renders: the change and the event that resolved it. */
  key: string;
  /** The agent's slot, to find its rail card. */
  slot: Slot;
  source: AgentFamily;
  /** The node that merged: where the pulse arrives and its light stays. */
  id: string;
  /** Milliseconds from the merge to the latest event the state has applied, from event times. */
  age: number;
}

/** A module the rest contours rise over, and how many modules call into it. */
export interface FieldModule {
  id: string;
  callers: number;
}

/** True when two selections would draw the same field, so an event that changes neither skips a redraw. */
export function sameLights(a: readonly FieldLight[], b: readonly FieldLight[]): boolean {
  return (
    a.length === b.length &&
    a.every((light, index) => {
      const other = b[index];
      return (
        other !== undefined &&
        light.id === other.id &&
        light.marker === other.marker &&
        light.steady === other.steady &&
        light.source === other.source &&
        light.weight === other.weight &&
        light.pulses === other.pulses
      );
    })
  );
}
