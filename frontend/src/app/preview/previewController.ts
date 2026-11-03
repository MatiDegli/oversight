import type {
  PreviewCandidate,
  PreviewClock,
  PreviewController,
  PreviewIdentity,
  PreviewPort,
  PreviewView,
} from "./previewPort";
import { parsePreviewDetails, parsePreviewStatus, previewId } from "./previewTypes";
import type { PreviewStatus } from "./previewTypes";

const active = (status: PreviewStatus | null) =>
  status?.state === "starting" || status?.state === "ready";
const terminal = (status: PreviewStatus) => status.state === "stopped" || status.state === "failed";
const sameCandidate = (status: PreviewStatus, candidate: PreviewCandidate) =>
  status.run_id === candidate.run_id &&
  status.agent_id === candidate.agent_id &&
  status.revision === candidate.revision &&
  status.head_commit === candidate.head_commit;
const rank = { starting: 0, ready: 1, stopping: 2, stopped: 3, failed: 3 };

/** Dormant, injected controller. No transport, store, browser globals or persistence. */
export function createPreviewController(
  port: PreviewPort,
  clock: PreviewClock,
  identity: PreviewIdentity,
): PreviewController {
  return new Controller(port, clock, identity);
}
class Controller implements PreviewController {
  #port: PreviewPort;
  #clock: PreviewClock;
  #identity: PreviewIdentity;
  #token: string | null = null;
  #id: string | null = null;
  #candidate: PreviewCandidate | null = null;
  #epoch = 0;
  #disposed = false;
  #detailsEpoch: number | null = null;
  #quarantined = false;
  #cancelStart = false;
  #renewOff: (() => void) | null = null;
  #flight: Promise<void> | null = null;
  #closing: Promise<boolean> | null = null;
  #listeners = new Set<() => void>();
  #requests = new Set<AbortController>();
  #view: PreviewView = Object.freeze({
    visible: false,
    status: null,
    owned: false,
    pending: false,
    uncertain: false,
    frameUrl: null,
    notice: null,
  });
  constructor(port: PreviewPort, clock: PreviewClock, identity: PreviewIdentity) {
    this.#port = port;
    this.#clock = clock;
    this.#identity = { ...identity };
  }
  snapshot = () => this.#view;
  subscribe = (listener: () => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };
  #publish(patch: Partial<PreviewView>) {
    if (this.#disposed) return;
    this.#view = Object.freeze({ ...this.#view, ...patch, owned: this.#token !== null });
    for (const listener of this.#listeners) listener();
  }
  async #bounded<T>(request: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const abort = new AbortController();
    this.#requests.add(abort);
    let cancel: () => void = () => undefined;
    const deadline = new Promise<never>((_, reject) => {
      cancel = this.#clock.schedule(() => {
        abort.abort();
        reject(new Error("Preview request deadline"));
      }, 5_000);
    });
    try {
      return await Promise.race([request(abort.signal), deadline]);
    } finally {
      cancel();
      this.#requests.delete(abort);
    }
  }
  #cancelRenewal() {
    this.#renewOff?.();
    this.#renewOff = null;
  }
  #current(epoch: number) {
    return epoch === this.#epoch && !this.#disposed;
  }
  #cancelled() {
    return this.#cancelStart || this.#disposed;
  }
  #matches(status: PreviewStatus) {
    return (
      status.preview_id === this.#id &&
      this.#candidate !== null &&
      sameCandidate(status, this.#candidate) &&
      (!this.#view.status || status.change_id === this.#view.status.change_id)
    );
  }
  #accept(status: PreviewStatus) {
    const old = this.#view.status;
    if (old && (rank[status.state] < rank[old.state] || (terminal(old) && this.#token !== null)))
      return false;
    if (old && JSON.stringify(old) === JSON.stringify(status)) return false;
    this.#publish({ status, visible: true });
    if (!active(status)) {
      this.#cancelRenewal();
      if (this.#view.frameUrl) this.#publish({ uncertain: true, notice: "closure_unconfirmed" });
    }
    return true;
  }
  observe = (input: PreviewStatus) => {
    if (this.#disposed) return;
    const status = parsePreviewStatus(input);
    if (this.#token) {
      if (this.#matches(status)) this.#accept(status);
    } else if (!this.#flight && status.run_id === this.#identity.run) {
      if (this.#view.status?.preview_id === status.preview_id) this.#accept(status);
      else this.#publish({ status, visible: true, frameUrl: null, notice: null });
    }
  };
  #scheduleRenewal() {
    this.#cancelRenewal();
    if (this.#disposed || !this.#token || !active(this.#view.status) || this.#quarantined) return;
    this.#renewOff = this.#clock.schedule(() => {
      void this.#renew();
    }, 10_000);
  }
  async #renew() {
    const epoch = this.#epoch,
      id = this.#id,
      token = this.#token;
    if (!id || !token || !active(this.#view.status) || this.#disposed) return;
    try {
      const status = parsePreviewStatus(
        await this.#bounded((signal) => this.#port.renew(id, { lease_token: token }, signal)),
      );
      if (!this.#current(epoch)) return;
      if (!this.#matches(status)) throw new Error("Mismatched renewal");
      this.#accept(status);
      this.#scheduleRenewal();
    } catch {
      if (!this.#current(epoch)) return;
      this.#quarantined = true;
      this.#publish({ uncertain: true, notice: "renewal_failed" });
    }
  }
  refresh = async () => {
    const epoch = this.#epoch,
      id = this.#id;
    if (!id || !this.#token || this.#disposed || this.#closing || this.#detailsEpoch === epoch)
      return;
    this.#detailsEpoch = epoch;
    try {
      const details = parsePreviewDetails(
        await this.#bounded((signal) => this.#port.details(id, signal)),
      );
      if (!this.#current(epoch)) return;
      if (!this.#matches(details.status) || (this.#token && details.url?.includes(this.#token)))
        throw new Error("Invalid details");
      this.#accept(details.status);
      if (
        details.status.state === "ready" &&
        this.#view.status?.state === "ready" &&
        !this.#quarantined
      )
        this.#publish({ frameUrl: details.url });
    } catch {
      if (epoch === this.#epoch) {
        this.#quarantined = true;
        this.#cancelRenewal();
        this.#publish({ uncertain: true, notice: "closure_unconfirmed" });
      }
    } finally {
      if (this.#detailsEpoch === epoch) this.#detailsEpoch = null;
    }
  };
  see = (candidate: () => PreviewCandidate | null): Promise<void> => {
    if (this.#disposed || this.#flight || this.#closing || this.#quarantined)
      return Promise.resolve();
    const observed = this.#view.status;
    if (
      !this.#token &&
      observed &&
      (active(observed) ||
        observed.state === "stopping" ||
        observed.reason === "cleanup_unverified")
    ) {
      this.#publish({ visible: true });
      return Promise.resolve();
    }
    this.#cancelStart = false;
    this.#flight = this.#open(candidate).finally(() => {
      this.#flight = null;
    });
    return this.#flight;
  };
  async #open(getCandidate: () => PreviewCandidate | null) {
    this.#publish({ pending: true });
    if (this.#token && !(await this.#stop())) return;
    if (this.#cancelled()) return;
    // Read only AFTER authenticated drainage; the candidate may have changed while stopping.
    const candidate = getCandidate();
    if (
      !candidate ||
      candidate.run_id !== this.#identity.run ||
      !Number.isSafeInteger(candidate.revision) ||
      candidate.revision <= 0 ||
      !candidate.agent_id.trim() ||
      !candidate.head_commit.trim()
    ) {
      this.#publish({ pending: false });
      return;
    }
    const observed = {
      run_id: candidate.run_id,
      agent_id: candidate.agent_id,
      revision: candidate.revision,
      head_commit: candidate.head_commit,
    };
    const epoch = ++this.#epoch;
    this.#publish({
      visible: true,
      pending: true,
      status: null,
      notice: "opening",
      frameUrl: null,
    });
    try {
      const receipt = await this.#bounded((signal) =>
        this.#port.start(observed, signal).then((result) => {
          // Even a late receipt is drained; it never revives a disposed/changed controller.
          if (signal.aborted || this.#disposed || epoch !== this.#epoch) void this.#abandon(result);
          return result;
        }),
      );
      if (!previewId(receipt.preview_id) || !receipt.lease_token.trim())
        throw new Error("Invalid receipt");
      if (!this.#current(epoch)) return;
      this.#token = receipt.lease_token;
      this.#id = receipt.preview_id;
      this.#candidate = observed;
      this.#publish({ notice: null });
      await this.refresh();
      this.#publish({ pending: false });
      if (this.#cancelled()) await this.#stop();
      else this.#scheduleRenewal();
    } catch {
      this.#quarantined = true;
      this.#publish({ pending: false, uncertain: true, notice: "start_failed" });
    }
  }
  async #abandon(receipt: { preview_id: string; lease_token: string }) {
    if (!previewId(receipt.preview_id) || !receipt.lease_token) return;
    try {
      await this.#bounded((signal) =>
        this.#port.stop(receipt.preview_id, { lease_token: receipt.lease_token }, signal),
      );
    } catch {
      /* Expiry remains the fallback. */
    }
  }
  #stop(): Promise<boolean> {
    if (this.#closing) return this.#closing;
    this.#closing = this.#drain().finally(() => {
      this.#closing = null;
    });
    return this.#closing;
  }
  async #drain() {
    this.#cancelRenewal();
    ++this.#epoch;
    const id = this.#id,
      token = this.#token;
    if (!id || !token) return !this.#quarantined;
    const old = this.#view.status;
    this.#publish({
      pending: true,
      notice: "closure_unconfirmed",
      uncertain: true,
      status: old ? { ...old, state: "stopping", reason: "closed" } : null,
    });
    try {
      const status = parsePreviewStatus(
        await this.#bounded((signal) => this.#port.stop(id, { lease_token: token }, signal)),
      );
      if (!this.#matches(status) || !terminal(status) || status.reason === "cleanup_unverified")
        throw new Error("Unverified drainage");
      this.#token = null;
      this.#id = null;
      this.#candidate = null;
      this.#quarantined = false;
      this.#publish({ status, frameUrl: null, uncertain: false, pending: false, notice: null });
      return true;
    } catch {
      this.#quarantined = true;
      this.#publish({
        pending: false,
        uncertain: true,
        notice: "closure_unconfirmed",
        status: old ? { ...old, state: "failed", reason: "cleanup_unverified" } : null,
      });
      return false;
    }
  }
  close = async () => {
    this.#cancelStart = true;
    await this.#flight;
    if (this.#disposed) return;
    if (!this.#token) {
      if (!this.#quarantined) this.#publish({ visible: false, pending: false });
      return;
    }
    if (await this.#stop()) this.#publish({ visible: false });
  };
  navigate = () => this.close();
  setIdentity = async (identity: PreviewIdentity) => {
    if (identity.run === this.#identity.run && identity.source === this.#identity.source) return;
    this.#identity = { ...identity };
    ++this.#epoch;
    await this.close();
    if (!this.#token && !this.#quarantined) this.#publish({ status: null });
  };
  dispose = () => {
    if (this.#disposed) return;
    this.#disposed = true;
    this.#cancelStart = true;
    ++this.#epoch;
    this.#cancelRenewal();
    for (const request of this.#requests) request.abort();
    const id = this.#id,
      token = this.#token;
    this.#token = null;
    this.#listeners.clear();
    if (id && token) void this.#abandon({ preview_id: id, lease_token: token });
  };
}
