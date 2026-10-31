import "./inbox.css";

export { ImpactConeCard } from "./ImpactCone";
export type { ReviewPanelProps } from "./ReviewPanel";
export { decisionView, failureMessage, sendDecision, startAttempt } from "./decisions";
export type { DecisionAction, DecisionAttempt, DecisionClient, DecisionView } from "./decisions";
export { ChangeFactsRow } from "./ChangeFactsRow";

export { DecisionContext } from "./DecisionContext";
export { useAnswers } from "./answers";

export { selectConeWords } from "./coneWords";
export { candidateKey, currentCards, approvalLocked, approvalLockedCaption } from "./answers";

// Keep optional focus contents out of the eager public surface.
export const loadReviewPanel = () => import("./ReviewPanel");
export const loadDecisionInbox = () => import("./DecisionInbox");
export const loadReviewBody = () => import("./ReviewBody");
export const loadDecisionControls = () => import("./ReviewDecisionBar");
export { ReviewHeader } from "./ReviewHeader";
export { selectInboxSummary, selectChangeCard } from "./inboxSelectors";
