import type { ServerEvent } from "@contracts";
import { createStore } from "zustand/vanilla";

import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";
import type { State } from "./types";

export interface StateStore {
  state: State;
  applyEvents: (events: readonly ServerEvent[]) => void;
}

export function createStateStore(initialState: State = createInitialState()) {
  return createStore<StateStore>()((set) => ({
    state: initialState,
    applyEvents: (events) => {
      set(({ state }) => ({ state: events.reduce(applyEvent, state) }));
    },
  }));
}

export const stateStore = createStateStore();
