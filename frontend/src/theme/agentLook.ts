import type { CSSProperties } from "react";
import type { Slot } from "@contracts";

export type AgentFamily = `agent-${Lowercase<Slot>}`;
type SlotLook = {
  family: AgentFamily;
  text: `${AgentFamily}-text`;
  tint: string;
  border: string;
  fill: string;
  swatch: string;
  textClass: string;
};

/** One exhaustive identity map for CSS utilities, graph paint and field emitters. */
export const agentLook = {
  A: {
    family: "agent-a",
    text: "agent-a-text",
    tint: "bg-agent-a-tint text-agent-a-text",
    border: "border-agent-a",
    fill: "bg-agent-a",
    swatch: "bg-agent-a text-on-agent",
    textClass: "text-agent-a-text",
  },
  B: {
    family: "agent-b",
    text: "agent-b-text",
    tint: "bg-agent-b-tint text-agent-b-text",
    border: "border-agent-b",
    fill: "bg-agent-b",
    swatch: "bg-agent-b text-on-agent",
    textClass: "text-agent-b-text",
  },
  C: {
    family: "agent-c",
    text: "agent-c-text",
    tint: "bg-agent-c-tint text-agent-c-text",
    border: "border-agent-c",
    fill: "bg-agent-c",
    swatch: "bg-agent-c text-on-agent",
    textClass: "text-agent-c-text",
  },
  D: {
    family: "agent-d",
    text: "agent-d-text",
    tint: "bg-agent-d-tint text-agent-d-text",
    border: "border-agent-d",
    fill: "bg-agent-d",
    swatch: "bg-agent-d text-on-agent",
    textClass: "text-agent-d-text",
  },
  E: {
    family: "agent-e",
    text: "agent-e-text",
    tint: "bg-agent-e-tint text-agent-e-text",
    border: "border-agent-e",
    fill: "bg-agent-e",
    swatch: "bg-agent-e text-on-agent",
    textClass: "text-agent-e-text",
  },
  F: {
    family: "agent-f",
    text: "agent-f-text",
    tint: "bg-agent-f-tint text-agent-f-text",
    border: "border-agent-f",
    fill: "bg-agent-f",
    swatch: "bg-agent-f text-on-agent",
    textClass: "text-agent-f-text",
  },
} as const satisfies Record<Slot, SlotLook>;

export const agentSlots = Object.keys(agentLook) as Slot[];
export function slotForFamily(family: AgentFamily): Slot {
  const slot = agentSlots.find((slot) => agentLook[slot].family === family);
  if (!slot) throw new Error(`Missing slot family: ${family}`);
  return slot;
}

/** Shared CSS context for surfaces whose paint is owned by a stylesheet. */
export function agentSlotStyle(slot: Slot | undefined): CSSProperties {
  return slot
    ? ({
        "--slot-graphic": `var(--${agentLook[slot].family})`,
        "--slot-text": `var(--${agentLook[slot].text})`,
      } as CSSProperties)
    : {};
}
