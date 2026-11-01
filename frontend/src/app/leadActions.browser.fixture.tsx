import { createRoot } from "react-dom/client";
import type { ServerEvent } from "@contracts";
import { stateStore } from "../state";
import { DecisionInbox, DecisionContext } from "../review";
import { App } from "./App";
import {
  leadActionsBase,
  cartPlan,
  orderPlan,
  fourthTask,
  queueItem,
  planRequest,
} from "./leadActions.fixture";

function emit(events: ServerEvent[]) {
  stateStore.getState().applyEvents(
    events.map((e, index) => ({
      ...e,
      seq: stateStore.getState().state.lastSeq + index + 1,
      ts: "2026-10-27T19:00:00Z",
    })),
  );
}
function update(plan: Partial<typeof orderPlan>, queue?: Partial<typeof queueItem>) {
  if (queue) emit([{ seq: 0, ts: "", type: "queue.updated", payload: { ...queueItem, ...queue } }]);
  emit([{ seq: 0, ts: "", type: "plan.updated", payload: { ...orderPlan, ...plan } }]);
}
stateStore.getState().applyEvents(leadActionsBase);
let proposedState = stateStore.getState().state;
function propose() {
  emit([...planRequest(cartPlan, 2), ...planRequest(orderPlan, 3)]);
  proposedState = stateStore.getState().state;
}
let runs = 0;
function restart(slots = 2) {
  stateStore.getState().applyEvents(
    leadActionsBase.map((e) =>
      e.type === "run.started"
        ? {
            ...e,
            payload: {
              ...e.payload,
              run_id: `run-20261101-19000${String(++runs)}`,
              slots: e.payload.slots.slice(0, slots),
            },
          }
        : e,
    ),
  );
  propose();
}
Object.assign(window, {
  leadActions: {
    readonly,
    emit,
    update,
    propose,
    restart,
    cartPlan,
    orderPlan,
    fourthTask,
    queueItem,
    store: stateStore,
  },
});
const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
const appRoot = createRoot(root);
appRoot.render(<App />);
export function readonly() {
  appRoot.render(
    <DecisionContext value={{ replayOf: "hand-written fixture" }}>
      <DecisionInbox state={proposedState} />
    </DecisionContext>,
  );
}

declare global {
  interface Window {
    leadActions: {
      emit: typeof emit;
      update: typeof update;
      propose: typeof propose;
      restart: typeof restart;
      readonly: typeof readonly;
      cartPlan: typeof cartPlan;
      orderPlan: typeof orderPlan;
      fourthTask: typeof fourthTask;
      queueItem: typeof queueItem;
      store: typeof stateStore;
    };
  }
}
