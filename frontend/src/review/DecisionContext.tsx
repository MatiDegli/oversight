import { createContext, useContext } from "react";
import type { Plan, QueuedTask } from "@contracts";
import type { State } from "../state";
import type { DecisionAction, DecisionAttempt } from "./decisions";

export type PlanAction = "queue" | "reject" | "cancel";
export interface PlanAttempt {
  run: State["run"];
  planId: string;
  action: PlanAction;
  revision: number;
  queueRevision?: number;
  queueId?: string;
  phase: "pending" | "failed";
  message?: string;
}

export const DecisionContext = createContext<{
  attempts?: Readonly<Record<string, DecisionAttempt>>;
  onDecide?: (id: string, action: DecisionAction, comment: string) => void;
  revokeGrant?: (id: string) => Promise<unknown>;
  planActions?: {
    attempts: Readonly<Record<string, PlanAttempt>>;
    send: (plan: Plan, action: PlanAction, queue?: QueuedTask, comment?: string) => void;
  };
  replayOf?: string;
}>({});
export const useDecisions = () => useContext(DecisionContext);

export const replayDecisionWords = (source?: string) =>
  `${source ? `Replay of a ${source}` : "Replay"}: nothing here sends a decision. The decision in the log shows when it arrives.`;
