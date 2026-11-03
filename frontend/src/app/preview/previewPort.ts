import type { AgentId, RunId, NonEmptyText, PreviewCreated, PreviewTokenRequest } from "@contracts";
import type { PreviewDetails, PreviewStatus } from "./previewTypes";

export interface PreviewCandidate {
  change_id?: string;
  run_id: RunId;
  agent_id: AgentId;
  revision: number;
  head_commit: NonEmptyText;
}
export interface PreviewIdentity {
  run: RunId;
  source: string;
}
/** Future HTTP boundary, injected only. This front implements no routes or transport.
 * start: POST /previews, with exactly the observed candidate keys; positive revision.
 * details: GET /previews/{id}. Only this trusted boundary supplies a frame URL.
 * renew: POST /previews/{id}/lease; stop: POST /previews/{id}/stop.
 * Token request bodies contain ONLY lease_token, no command, URL, reason, profile or target.
 * A successful terminal stop result certifies verified drainage. Unverified cleanup MUST
 * reject: a terminal-looking enum alone is not proof that an app died.
 * All operations honor AbortSignal; deadline is 5 s, renewal every 10 s, one flight per kind,
 * no retries. A start/stop timeout quarantines the preview until verified drainage.
 */
export interface PreviewPort {
  start(input: PreviewCandidate, signal: AbortSignal): Promise<PreviewCreated>;
  details(id: string, signal: AbortSignal): Promise<PreviewDetails>;
  renew(id: string, body: PreviewTokenRequest, signal: AbortSignal): Promise<PreviewStatus>;
  stop(id: string, body: PreviewTokenRequest, signal: AbortSignal): Promise<PreviewStatus>;
}
export interface PreviewClock {
  schedule(callback: () => void, delay: number): () => void;
}
/** Controller/UI memory, never a wire type. Tokens are absent even from this view. */
export interface PreviewView {
  visible: boolean;
  status: PreviewStatus | null;
  owned: boolean;
  pending: boolean;
  uncertain: boolean;
  frameUrl: string | null;
  notice: "opening" | "start_failed" | "closure_unconfirmed" | "renewal_failed" | null;
}
export interface PreviewController {
  snapshot: () => PreviewView;
  subscribe: (listener: () => void) => () => void;
  see: (candidate: () => PreviewCandidate | null) => Promise<void>;
  observe: (status: PreviewStatus) => void;
  refresh: () => Promise<void>;
  close: () => Promise<void>;
  navigate: () => Promise<void>;
  setIdentity: (identity: PreviewIdentity) => Promise<void>;
  /** Page disappearance: best effort only; server lease expiry covers lost delivery. */
  dispose: () => void;
}
