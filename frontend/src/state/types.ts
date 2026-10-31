import type {
  AgentFocus,
  AgentStatus,
  AgentStep,
  ChangeGraph,
  CrossReview,
  ChangeFacts,
  SuiteExecution,
  ChangeProposed,
  ChangeResolved,
  ChangeUpdated,
  ErrorReport,
  GraphSnapshot,
  ModelAttemptFailed,
  RunStarted,
  UsageTick,
} from "@contracts";

import type { DecisionEvent } from "./decisionProjection";
import type { NeedsYou } from "./needsYou";

/** Retained envelope order; older in-memory fixtures can omit the timestamp. */
export interface EventPosition {
  seq: number;
  ts?: string;
}

export interface ChangeState {
  change_id: string;
  agent_id: string;
  status: "editing" | "ready_for_review" | "revising" | "conflict" | "approved" | "rejected";
  files: Record<string, Pick<ChangeUpdated, "file" | "author" | "node_ids">>;
  /** Event order of each path's contents, independently of other files in the change. */
  fileUpdateSeq?: Record<string, number>;
  graph: ChangeGraph;
  files_changed: number;
  lines_added: number;
  lines_removed: number;
  proposal: ChangeProposed | null;
  resolution: ChangeResolved | null;
  proposalEvent?: EventPosition;
  resolutionEvent?: EventPosition | null;
  /** Applied event order for recorded file contents after successive merges. */
  resolutionSeq?: number;
  /** Evidence survives new revisions; only the current note is invalidated. */
  reviews: { seq: number; review: CrossReview }[];
  crossReview: CrossReview | null;
  crossReviewRound: ChangeResolved | null;
  needsYou: NeedsYou | null;
  personActed: boolean;
}

export type ModelWait = Pick<ModelAttemptFailed, "attempt" | "max_attempts" | "cause"> & {
  seq: number;
  wait_s: number;
};

export interface AgentMessage {
  seq: number;
  ts: string;
  text: string | null;
}

export interface State {
  run: RunStarted | null;
  runStartedAt: string | null;
  grantEvents: Extract<
    import("@contracts").ServerEvent,
    { type: "grant.created" | "grant.revoked" }
  >[];
  lastSeq: number;
  gap: boolean;
  graph: GraphSnapshot | null;
  agents: Record<string, AgentStatus>;
  steps: Record<string, AgentStep[]>;
  modelWaits: Record<string, ModelWait>;
  messages: Record<string, AgentMessage[]>;
  /** When each step's event arrived, by agent then step number: the age the card shows. */
  stepTimes: Record<string, Record<number, string>>;
  stepSeqs: Record<string, Record<number, number>>;
  /**
   * The timestamp of the latest event applied: "now" for every age, so a live run and a replay
   * read the same and nothing depends on the reader's clock. Null before the first event.
   */
  clock: string | null;
  focus: Record<string, AgentFocus>;
  /** One most recently read node per agent, independent of subsequent edit focus. */
  lastReads: Record<string, string>;
  /** Observed reads, in first-read order, for the life of each change. */
  changeReads: Record<string, string[]>;
  changes: Record<string, ChangeState>;
  usage: UsageTick | null;
  spendUnknown: boolean;
  /**
   * What each agent has cost so far: the sum of its usage ticks. Null once any of its ticks
   * reported no cost, because an unreported cost is unknown and never 0. An agent with no tick
   * has no entry.
   */
  agentCosts: Record<string, number | null>;
  errors: ErrorReport[];
  /** Envelopes parallel to errors, including its needs-you deduplication. */
  errorEvents: EventPosition[];
  testExecutions: Record<number, SuiteExecution>;
  changeFacts: Record<string, ChangeFacts[]>;
  /** Run-local inbox evidence; retained even when the current proposal is replaced. */
  decisionEvents: DecisionEvent[];
}

export function createInitialState(): State {
  return {
    run: null,
    runStartedAt: null,
    grantEvents: [],
    lastSeq: 0,
    gap: false,
    graph: null,
    agents: {},
    steps: {},
    modelWaits: {},
    messages: {},
    stepTimes: {},
    stepSeqs: {},
    clock: null,
    focus: {},
    lastReads: {},
    changeReads: {},
    changes: {},
    usage: null,
    spendUnknown: false,
    agentCosts: {},
    errors: [],
    errorEvents: [],
    testExecutions: {},
    changeFacts: {},
    decisionEvents: [],
  };
}

export function createChange(change_id: string, agent_id: string): ChangeState {
  return {
    change_id,
    agent_id,
    status: "editing",
    files: {},
    graph: {
      nodes_added: [],
      nodes_modified: [],
      nodes_removed: [],
      edges_added: [],
      edges_removed: [],
    },
    files_changed: 0,
    lines_added: 0,
    lines_removed: 0,
    proposal: null,
    resolution: null,
    reviews: [],
    crossReview: null,
    crossReviewRound: null,
    needsYou: null,
    personActed: false,
  };
}
