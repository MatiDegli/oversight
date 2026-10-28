import type {
  AgentFocus,
  AgentStatus,
  AgentStep,
  ChangeGraph,
  ChangeFacts,
  SuiteExecution,
  ChangeProposed,
  ChangeResolved,
  ChangeUpdated,
  ErrorReport,
  GraphSnapshot,
  RunStarted,
  UsageTick,
} from "@contracts";

export interface ChangeState {
  change_id: string;
  agent_id: string;
  status: "editing" | "ready_for_review" | "revising" | "conflict" | "approved" | "rejected";
  files: Record<string, Pick<ChangeUpdated, "file" | "author" | "node_ids">>;
  graph: ChangeGraph;
  files_changed: number;
  lines_added: number;
  lines_removed: number;
  proposal: ChangeProposed | null;
  resolution: ChangeResolved | null;
  /** Applied event order for recorded file contents after successive merges. */
  resolutionSeq?: number;
}

export interface State {
  run: RunStarted | null;
  lastSeq: number;
  gap: boolean;
  graph: GraphSnapshot | null;
  agents: Record<string, AgentStatus>;
  steps: Record<string, AgentStep[]>;
  /** When each step's event arrived, by agent then step number: the age the card shows. */
  stepTimes: Record<string, Record<number, string>>;
  /**
   * The timestamp of the latest event applied: "now" for every age, so a live run and a replay
   * read the same and nothing depends on the reader's clock. Null before the first event.
   */
  clock: string | null;
  focus: Record<string, AgentFocus>;
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
  testExecutions: Record<number, SuiteExecution>;
  changeFacts: Record<string, ChangeFacts[]>;
}

export function createInitialState(): State {
  return {
    run: null,
    lastSeq: 0,
    gap: false,
    graph: null,
    agents: {},
    steps: {},
    stepTimes: {},
    clock: null,
    focus: {},
    changes: {},
    usage: null,
    spendUnknown: false,
    agentCosts: {},
    errors: [],
    testExecutions: {},
    changeFacts: {},
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
  };
}
