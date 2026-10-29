import type { ServerEvent } from "@contracts";
import type { State } from "./types";
import { createChange } from "./types";

type Event = Extract<
  ServerEvent,
  { type: "agent.status" | "agent.step" | "agent.focus" | "usage.tick" }
>;

export function applyAgentEvent(state: State, next: State, event: Event): State {
  switch (event.type) {
    case "agent.status": {
      const status = event.payload;
      return {
        ...next,
        agents: { ...state.agents, [status.agent_id]: status },
        changes: {
          ...state.changes,
          [status.change_id]:
            state.changes[status.change_id] ?? createChange(status.change_id, status.agent_id),
        },
      };
    }
    case "agent.step": {
      const step = event.payload;
      return {
        ...next,
        steps: {
          ...state.steps,
          [step.agent_id]: [...(state.steps[step.agent_id] ?? []), step],
        },
        stepSeqs: {
          ...state.stepSeqs,
          [step.agent_id]: { ...state.stepSeqs[step.agent_id], [step.step]: event.seq },
        },
        stepTimes: {
          ...state.stepTimes,
          [step.agent_id]: { ...state.stepTimes[step.agent_id], [step.step]: event.ts },
        },
      };
    }
    case "agent.focus": {
      const focus = event.payload;
      const lastReads = { ...state.lastReads };
      if (focus.mode === "read") {
        const id = focus.node_ids[0];
        if (id) lastReads[focus.agent_id] = id;
        else Reflect.deleteProperty(lastReads, focus.agent_id);
      }
      return { ...next, focus: { ...state.focus, [focus.agent_id]: focus }, lastReads };
    }
    case "usage.tick": {
      const tick = event.payload;
      const before = state.agentCosts[tick.agent_id];
      // Unknown stays unknown: a later tick with a cost does not make the total known again.
      const cost = tick.cost_usd === null || before === null ? null : (before ?? 0) + tick.cost_usd;
      // The status names a profile; the tick's model is the provider's reported API id.
      const modelId = state.agents[tick.agent_id]?.model;
      const profile = state.run?.models.find((model) => model.id === modelId);
      return {
        ...next,
        usage: tick,
        agentCosts: { ...state.agentCosts, [tick.agent_id]: cost },
        spendUnknown:
          !profile || profile.provider === "meta" ? tick.cost_usd === null : state.spendUnknown,
      };
    }
  }
}
