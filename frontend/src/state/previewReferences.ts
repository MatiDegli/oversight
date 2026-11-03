import type { LeadView, StartPreview } from "@contracts";
import type { State } from "./types";

export type ResolvedPreview = StartPreview & { change_id: string };
export type PreviewResolution =
  { candidate: ResolvedPreview; reason: null } | { candidate: null; reason: string };
const resolutions = new WeakMap<State, WeakMap<LeadView, PreviewResolution>>();

/** Resolve the observed proposal, never a newer candidate carrying the same agent id. */
export function resolvePreviewReference(state: State, request: LeadView): PreviewResolution {
  // Revalidate every read, including activation after drainage. Only the returned identity is cached.
  const next = resolveCurrentPreview(state, request);
  let requests = resolutions.get(state);
  if (!requests) {
    requests = new WeakMap();
    resolutions.set(state, requests);
  }
  const previous = requests.get(request);
  if (
    previous &&
    previous.reason === next.reason &&
    previous.candidate?.run_id === next.candidate?.run_id &&
    previous.candidate?.agent_id === next.candidate?.agent_id &&
    previous.candidate?.change_id === next.candidate?.change_id &&
    previous.candidate?.revision === next.candidate?.revision &&
    previous.candidate?.head_commit === next.candidate?.head_commit
  )
    return previous;
  requests.set(request, next);
  return next;
}

function resolveCurrentPreview(state: State, request: LeadView): PreviewResolution {
  const unavailable = (reason: string): PreviewResolution => ({ candidate: null, reason });
  if (request.view.kind !== "preview") return unavailable("This is not a preview reference.");
  if (!state.run || state.gap || request.through_seq > state.lastSeq)
    return unavailable("The run evidence is incomplete.");
  // Membership in the displayed run prevents an old retained Chat suggestion crossing a run reset.
  if (!(state.leadViews[request.agent_id] ?? []).some((event) => event.payload === request))
    return unavailable("This suggestion is unavailable in this run.");
  const agent = state.agents[request.view.agent_id];
  const change = agent && state.changes[agent.change_id];
  const proposal = change?.proposal;
  if (
    !agent ||
    !change ||
    !proposal ||
    proposal.agent_id !== agent.agent_id ||
    proposal.change_id !== agent.change_id ||
    change.agent_id !== agent.agent_id
  )
    return unavailable("The referenced change has no current proposal.");
  if (agent.state !== "ready_for_review" && agent.state !== "halted")
    return unavailable("The referenced agent is not ready for preview.");
  const observed = state.decisionEvents.findLast(
    (event) =>
      event.type === "agent.status" &&
      event.payload.agent_id === agent.agent_id &&
      event.seq <= request.through_seq,
  );
  if (
    observed?.type !== "agent.status" ||
    observed.payload.change_id !== change.change_id ||
    (observed.payload.state !== "ready_for_review" && observed.payload.state !== "halted")
  )
    return unavailable("The agent was not ready at the suggestion’s observed horizon.");
  const seq = change.proposalEvent?.seq;
  const pinned = state.decisionEvents.find(
    (event) => event.type === "change.proposed" && event.seq === seq,
  );
  if (
    pinned?.type !== "change.proposed" ||
    pinned.payload.agent_id !== agent.agent_id ||
    pinned.payload.change_id !== change.change_id ||
    pinned.payload.revision !== proposal.revision ||
    pinned.payload.head_commit !== proposal.head_commit
  )
    return unavailable("The suggested revision is no longer available.");
  if (
    !seq ||
    seq > request.through_seq ||
    !Number.isSafeInteger(proposal.revision) ||
    proposal.revision <= 0 ||
    !proposal.head_commit.trim()
  )
    return unavailable("The suggested revision is no longer available.");
  if (
    Object.values(change.fileUpdateSeq ?? {}).some((update) => update > seq) ||
    (change.resolution && change.resolution.merge !== "conflict")
  )
    return unavailable("The referenced change was edited or resolved.");
  return {
    candidate: {
      run_id: state.run.run_id,
      agent_id: agent.agent_id,
      change_id: change.change_id,
      revision: proposal.revision,
      head_commit: proposal.head_commit,
    },
    reason: null,
  };
}
