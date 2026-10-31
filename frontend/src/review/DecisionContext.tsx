import { createContext, useContext } from "react";
import type { DecisionAction, DecisionAttempt } from "./decisions";

export const DecisionContext = createContext<{
  attempts?: Readonly<Record<string, DecisionAttempt>>;
  onDecide?: (id: string, action: DecisionAction, comment: string) => void;
  revokeGrant?: (id: string) => Promise<unknown>;
  replayOf?: string;
}>({});
export const useDecisions = () => useContext(DecisionContext);

export const replayDecisionWords = (source?: string) =>
  `${source ? `Replay of a ${source}` : "Replay"}: nothing here sends a decision. The decision in the log shows when it arrives.`;
