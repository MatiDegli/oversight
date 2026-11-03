// Test-only shared authority. No executable adapter, model calls, network or persistence.
import type { PreviewCandidate, PreviewClock, PreviewPort } from "./previewPort";
import { parsePreviewDetails, parsePreviewStatus } from "./previewTypes";
import type { PreviewDetails, PreviewReason, PreviewState, PreviewStatus } from "./previewTypes";

export const fakeCandidate: PreviewCandidate = {
  run_id: "run-preview",
  agent_id: "agent-a",
  revision: 1,
  head_commit: "4ca5596e0d1b2c3a",
};
export function fakeStatus(
  state: PreviewState = "starting",
  reason: PreviewReason | null = null,
): PreviewStatus {
  return parsePreviewStatus({
    preview_id: `preview-${"0".repeat(31)}1`,
    ...fakeCandidate,
    change_id: "change-agent-a",
    state,
    reason,
  });
}
export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
export class PreviewFake implements PreviewPort {
  readonly calls: { kind: string; id?: string; candidate?: PreviewCandidate }[] = [];
  readonly frameUrl = "https://preview-frame.invalid/app";
  launchState: "starting" | "ready" = "ready";
  stopMode: "verified" | "reject" | "unverified" | "lost" = "verified";
  renewalRejects = false;
  gate: ReturnType<typeof deferred<undefined>> | null = null;
  #status: PreviewStatus | null = null;
  #token: string | null = null;
  #serial = 0;
  #expiry: (() => void) | null = null;
  #clock: PreviewClock | undefined;
  constructor(clock?: PreviewClock) {
    this.#clock = clock;
  }
  publicStatus() {
    return this.#status ? { ...this.#status } : null;
  }
  #lease() {
    this.#expiry?.();
    this.#expiry =
      this.#clock?.schedule(() => {
        this.expire();
      }, 30_000) ?? null;
  }
  expire() {
    if (this.#status && this.#status.state !== "stopped") this.publish("stopped", "lease_expired");
    this.#token = null;
  }
  publish(state: PreviewState, reason: PreviewReason | null) {
    this.#status = parsePreviewStatus({ ...(this.#status ?? fakeStatus()), state, reason });
    return this.publicStatus();
  }
  start(input: PreviewCandidate, _signal: AbortSignal) {
    _signal.throwIfAborted();
    if (
      Object.keys(input).sort().join() !== "agent_id,head_commit,revision,run_id" ||
      !Number.isSafeInteger(input.revision) ||
      input.revision <= 0
    )
      return Promise.reject(new Error("Invalid start"));
    if (this.#token) return Promise.reject(new Error("In use"));
    this.calls.push({ kind: "start", candidate: { ...input } });
    const id = `preview-${(++this.#serial).toString(16).padStart(32, "0")}`;
    this.#token = `fake-private-${String(this.#serial)}`;
    this.#status = parsePreviewStatus({
      ...input,
      preview_id: id,
      change_id: `change-${input.agent_id}`,
      state: this.launchState,
      reason: null,
    });
    this.#lease();
    return Promise.resolve({ preview_id: id, lease_token: this.#token });
  }
  details(id: string, _signal: AbortSignal): Promise<PreviewDetails> {
    _signal.throwIfAborted();
    this.calls.push({ kind: "details", id });
    if (!this.#status || this.#status.preview_id !== id) return Promise.reject(new Error("Absent"));
    return Promise.resolve(
      parsePreviewDetails({
        status: this.#status,
        url: this.#status.state === "ready" ? this.frameUrl : null,
      }),
    );
  }
  #authorize(id: string, body: { lease_token: string }) {
    if (
      !this.#status ||
      id !== this.#status.preview_id ||
      !this.#token ||
      Object.keys(body).join() !== "lease_token" ||
      body.lease_token !== this.#token
    )
      throw new Error("Not owner");
  }
  renew(id: string, body: { lease_token: string }, _signal: AbortSignal) {
    _signal.throwIfAborted();
    this.#authorize(id, body);
    this.calls.push({ kind: "renew", id });
    if (this.renewalRejects) return Promise.reject(new Error("Rejected"));
    this.#lease();
    return Promise.resolve(parsePreviewStatus(this.#status));
  }
  async stop(id: string, body: { lease_token: string }, _signal: AbortSignal) {
    _signal.throwIfAborted();
    this.#authorize(id, body);
    this.calls.push({ kind: "stop", id });
    if (this.stopMode === "lost") return new Promise<PreviewStatus>(() => undefined);
    if (this.gate) await this.gate.promise;
    if (this.stopMode === "reject") throw new Error("Private child output must never be rendered");
    if (this.stopMode === "unverified") {
      this.publish("failed", "cleanup_unverified");
      throw new Error("Drainage not verified");
    }
    this.#expiry?.();
    this.#token = null;
    return parsePreviewStatus(this.publish("stopped", "closed"));
  }
}

/** Independent server time: advancing a page's clock cannot renew or expire another tab. */
export class PreviewFakeClock implements PreviewClock {
  #now = 0;
  #jobs = new Set<{ at: number; callback: () => void }>();
  schedule(callback: () => void, delay: number) {
    const job = { at: this.#now + delay, callback };
    this.#jobs.add(job);
    return () => {
      this.#jobs.delete(job);
    };
  }
  advance(milliseconds: number) {
    this.#now += milliseconds;
    for (const job of [...this.#jobs])
      if (job.at <= this.#now) {
        this.#jobs.delete(job);
        job.callback();
      }
  }
}
