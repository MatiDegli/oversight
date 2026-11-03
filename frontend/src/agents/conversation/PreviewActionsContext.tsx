import { createContext } from "react";
import type { LeadView } from "@contracts";
import type { State, ResolvedPreview } from "../../state";

export interface PreviewActions {
  state: State;
  readOnlyReason?: string;
  readState: () => State;
  onPreview?: (request: LeadView) => void;
  onReview?: (candidate: ResolvedPreview) => void;
}
/** Agents-owned capability seam; no app imports or authority-changing commands. */
export const PreviewActionsContext = createContext<PreviewActions | null>(null);
