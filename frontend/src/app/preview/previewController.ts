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
import {
  sameOwnershipCandidate,
  type PreviewOwnership,
  type PreviewOwnershipStore,
} from "./previewOwnership";

const active = (status: PreviewStatus | null) =>
  status?.state === "starting" || status?.state === "ready";
const terminal = (status: PreviewStatus) => status.state === "stopped" || status.state === "failed";
const confirmed = (status: PreviewStatus) =>
  terminal(status) && status.reason !== "cleanup_unverified";
const sameCandidate = (status: PreviewStatus, candidate: PreviewCandidate) =>
  status.run_id === candidate.run_id &&
  (candidate.change_id === undefined || status.change_id === candidate.change_id) &&
  status.agent_id === candidate.agent_id &&
  status.revision === candidate.revision &&
  status.head_commit === candidate.head_commit;
const rank = { starting: 0, ready: 1, stopping: 2, stopped: 3, failed: 3 };
const candidateKey = (candidate: PreviewCandidate) =>
  JSON.stringify([candidate.run_id, candidate.agent_id, candidate.revision, candidate.head_commit]);

/** Injected controller. Browser storage is optional and stays behind a private port. */
export function createPreviewController(
  port: PreviewPort,
  clock: PreviewClock,
  identity: PreviewIdentity,
  ownership?: PreviewOwnershipStore,
): PreviewController & Required<Pick<PreviewController, "recover">> {
  return new Controller(port, clock, identity, ownership);
}
class Controller implements PreviewController {
  #port: PreviewPort;
  #clock: PreviewClock;
  #identity: PreviewIdentity;
  #ownership?: PreviewOwnershipStore;
  #saved: PreviewOwnership | null = null;
  #recovery: Promise<void> | null = null;
  #recoveryChecked = false;
  #recovered = false;
  #resume = false;
  #observedCandidates = new Set<string>();
  #token: string | null = null;
  #id: string | null = null;
  #candidate: PreviewCandidate | null = null;
  #epoch = 0;
  #disposed = false;
  #dismissed = false;
  #detailsEpoch: number | null = null;
  #quarantined = false;
  #startDeadlinePassed = false;
  #startDeadlineOff: (() => void) | null = null;
  #cancelStart = false;
  #renewOff: (() => void) | null = null;
  #flight: Promise<void> | null = null;
  #closing: Promise<boolean> | null = null;
  #listeners = new Set<() => void>();
  #requests = new Set<AbortController>();
  #pendingStops = new Map<string, Promise<PreviewStatus>>();
  #view: PreviewView = Object.freeze({
    visible: false,
    status: null,
    owned: false,
    pending: false,
    uncertain: false,
    frameUrl: null,
    notice: null,
  });
  constructor(
    port: PreviewPort,
    clock: PreviewClock,
    identity: PreviewIdentity,
    ownership?: PreviewOwnershipStore,
  ) {
    this.#port = port;
    this.#clock = clock;
    this.#identity = { ...identity };
    this.#ownership = ownership;
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
  async #bounded<T>(
    request: (signal: AbortSignal) => Promise<T>,
    abortOnDispose = true,
  ): Promise<T> {
    const abort = new AbortController();
    if (abortOnDispose) this.#requests.add(abort);
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
  #clearSaved() {
    if (this.#saved) {
      try {
        this.#ownership?.clear(this.#saved);
      } catch {
        /* Memory authority is independent. */
      }
    }
    this.#saved = null;
  }
  #discardRecovery(status?: PreviewStatus) {
    const candidate = this.#saved?.candidate ?? this.#candidate;
    if (candidate) this.#observedCandidates.add(candidateKey(candidate));
    this.#clearSaved();
    this.#cancelRenewal();
    this.#token = null;
    this.#id = null;
    this.#recovered = this.#resume = false;
    this.#quarantined = status?.reason === "cleanup_unverified";
    this.#publish({ frameUrl: null, pending: false, uncertain: this.#quarantined });
  }
  recover = (getCandidate: () => PreviewCandidate | null): Promise<void> => {
    if (this.#recovery) return this.#recovery;
    if (
      this.#disposed ||
      this.#recoveryChecked ||
      this.#token ||
      this.#flight ||
      !this.#ownership ||
      !this.#identity.run ||
      this.#identity.source !== "live"
    )
      return Promise.resolve();
    this.#recoveryChecked = true;
    try {
      this.#saved = this.#ownership.load(this.#identity);
    } catch {
      return Promise.resolve();
    }
    if (!this.#saved) return Promise.resolve();
    this.#cancelStart = false;
    const flight = this.#recover(this.#saved, getCandidate).finally(() => {
      if (this.#recovery === flight) this.#recovery = null;
    });
    return (this.#recovery = flight);
  };
  async #recover(record: PreviewOwnership, getCandidate: () => PreviewCandidate | null) {
    const epoch = this.#epoch;
    this.#dismissed = true;
    try {
      if (!sameOwnershipCandidate(record.candidate, getCandidate())) throw new Error("Stale owner");
      const details = parsePreviewDetails(
        await this.#bounded((signal) => this.#port.details(record.preview_id, signal)),
      );
      if (!this.#current(epoch)) return;
      if (
        details.status.preview_id !== record.preview_id ||
        !sameCandidate(details.status, record.candidate) ||
        !sameOwnershipCandidate(record.candidate, getCandidate()) ||
        (this.#view.status?.preview_id === record.preview_id &&
          rank[this.#view.status.state] > rank[details.status.state]) ||
        details.url?.includes(record.lease_token)
      )
        throw new Error("Stale details");
      this.#publish({ status: details.status, visible: false, frameUrl: null });
      if (!active(details.status)) {
        this.#discardRecovery(details.status);
        return;
      }
      this.#candidate = { ...record.candidate };
      this.#id = record.preview_id;
      this.#token = record.lease_token;
      this.#recovered = this.#resume = true;
      if (this.#cancelStart) {
        await this.#stop();
        return;
      }
      await this.#renew();
      if (this.#current(epoch)) {
        if (!sameOwnershipCandidate(record.candidate, getCandidate())) this.#discardRecovery();
        else {
          if (this.#token) this.#observedCandidates.delete(candidateKey(record.candidate));
          this.#publish({});
        }
      }
    } catch {
      if (this.#current(epoch)) this.#discardRecovery(this.#view.status ?? undefined);
    }
  }
  #accept(status: PreviewStatus) {
    const old = this.#view.status;
    if (old && rank[status.state] < rank[old.state]) return false;
    if (old && JSON.stringify(old) === JSON.stringify(status)) return false;
    if (terminal(status)) this.#clearSaved();
    if (this.#recovered && !active(status)) {
      this.#publish({ status });
      this.#discardRecovery(status);
      return true;
    }
    if (this.#token && confirmed(status)) {
      this.#release(status);
      return true;
    }
    this.#publish({ status, visible: !this.#dismissed });
    if (!active(status)) {
      this.#cancelRenewal();
      if (this.#view.frameUrl) this.#publish({ uncertain: true, notice: "closure_unconfirmed" });
    }
    return true;
  }
  #release(status: PreviewStatus) {
    this.#clearSaved();
    this.#recovered = this.#resume = false;
    this.#cancelRenewal();
    this.#startDeadlineOff?.();
    ++this.#epoch;
    this.#token = null;
    this.#id = null;
    this.#candidate = null;
    this.#quarantined = false;
    this.#publish({
      status,
      visible: !this.#dismissed,
      frameUrl: null,
      uncertain: false,
      pending: false,
      notice: null,
    });
  }
  observe = (input: PreviewStatus) => {
    if (this.#disposed) return;
    const status = parsePreviewStatus(input);
    if (this.#token) {
      if (this.#matches(status)) this.#accept(status);
    } else if (!this.#flight && status.run_id === this.#identity.run) {
      const previous = this.#view.status;
      // A late duplicate after this owner's verified release is still that release.
      // First terminal arrivals and failed recovery already retain observer refusal.
      if (previous?.preview_id !== status.preview_id || !confirmed(previous))
        this.#observedCandidates.add(candidateKey(status));
      // After the start deadline, the run's single slot can also prove that a
      // different preview holds it, or that the run's preview has ended.
      if (
        this.#quarantined &&
        this.#candidate &&
        ((sameCandidate(status, this.#candidate) &&
          (!this.#id || status.preview_id === this.#id) &&
          confirmed(status)) ||
          (this.#startDeadlinePassed &&
            (confirmed(status) ||
              (!sameCandidate(status, this.#candidate) &&
                (active(status) || status.state === "stopping")))))
      ) {
        this.#release(status);
        return;
      }
      if (this.#view.status?.preview_id === status.preview_id) this.#accept(status);
      else this.#publish({ status, visible: !this.#dismissed, frameUrl: null, notice: null });
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
      if (this.#recovered) {
        this.#discardRecovery();
        return;
      }
      this.#quarantined = true;
      this.#publish({ uncertain: true, notice: "renewal_failed" });
    }
  }
  refresh = async () => {
    const epoch = this.#epoch,
      id = this.#id;
    if (
      !id ||
      !this.#token ||
      this.#resume ||
      this.#disposed ||
      this.#closing ||
      this.#detailsEpoch === epoch
    )
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
    if (this.#recovery) return this.#recovery.then(() => this.see(candidate));
    if (this.#disposed || this.#flight || this.#closing) return Promise.resolve();
    this.#dismissed = false;
    if (
      this.#resume &&
      this.#token &&
      this.#candidate &&
      sameOwnershipCandidate(this.#candidate, candidate())
    ) {
      this.#resume = this.#recovered = false;
      this.#publish({ visible: true });
      return this.refresh();
    }
    if (this.#quarantined) {
      this.#publish({
        visible: true,
        ...(!this.#token && { notice: "start_failed", uncertain: true }),
      });
      return Promise.resolve();
    }
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
    if (this.#observedCandidates.has(candidateKey(candidate))) {
      this.#publish({ pending: false, visible: true, frameUrl: null });
      return;
    }
    const observed = {
      run_id: candidate.run_id,
      agent_id: candidate.agent_id,
      revision: candidate.revision,
      head_commit: candidate.head_commit,
    };
    const epoch = ++this.#epoch;
    this.#startDeadlineOff?.();
    this.#startDeadlinePassed = false;
    this.#startDeadlineOff = this.#clock.schedule(() => {
      this.#startDeadlinePassed = true;
    }, 5_000);
    this.#candidate = {
      ...observed,
      ...(candidate.change_id && { change_id: candidate.change_id }),
    };
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
      this.#candidate = {
        ...observed,
        ...(candidate.change_id && { change_id: candidate.change_id }),
      };
      this.#saved = {
        identity: { ...this.#identity },
        candidate: { ...this.#candidate },
        preview_id: this.#id,
        lease_token: this.#token,
      };
      try {
        this.#ownership?.save(this.#saved);
      } catch {
        /* Memory ownership remains valid. */
      }
      this.#publish({ notice: null });
      await this.refresh();
      this.#publish({ pending: false });
      if (this.#cancelled()) await this.#stop();
      else this.#scheduleRenewal();
    } catch {
      if (!this.#current(epoch)) return;
      this.#quarantined = true;
      this.#publish({ pending: false, uncertain: true, notice: "start_failed" });
    }
  }
  #sendStop(id: string, token: string, signal: AbortSignal) {
    const pending = this.#pendingStops.get(id);
    if (pending) return pending;
    // A UI deadline does not finish a keepalive fetch. Retain that flight until
    // transport settles so later Close/disposal cannot dispatch a competing stop.
    const flight = this.#port
      .stop(id, { lease_token: token }, signal, { keepalive: true })
      .then(parsePreviewStatus)
      .then((status) => {
        if (
          this.#saved?.preview_id === status.preview_id &&
          sameCandidate(status, this.#saved.candidate) &&
          terminal(status)
        )
          this.#clearSaved();
        return status;
      })
      .finally(() => {
        if (this.#pendingStops.get(id) === flight) this.#pendingStops.delete(id);
      });
    this.#pendingStops.set(id, flight);
    return flight;
  }
  async #abandon(receipt: { preview_id: string; lease_token: string }) {
    if (!previewId(receipt.preview_id) || !receipt.lease_token) return;
    try {
      await this.#bounded(
        (signal) => this.#sendStop(receipt.preview_id, receipt.lease_token, signal),
        false,
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
    const epoch = ++this.#epoch;
    const id = this.#id,
      token = this.#token;
    if (!id || !token) return !this.#quarantined;
    this.#publish({
      pending: true,
      notice: "closure_unconfirmed",
      frameUrl: null,
      uncertain: true,
    });
    try {
      const status = parsePreviewStatus(
        await this.#bounded((signal) => this.#sendStop(id, token, signal), false),
      );
      if (
        status.preview_id === id &&
        this.#candidate &&
        sameCandidate(status, this.#candidate) &&
        terminal(status)
      )
        this.#clearSaved();
      if (!this.#current(epoch)) return !this.#token && !this.#quarantined;
      if (!this.#matches(status) || !confirmed(status)) throw new Error("Unverified drainage");
      this.#release(status);
      return true;
    } catch {
      if (!this.#current(epoch)) return !this.#token && !this.#quarantined;
      if (this.#recovered) this.#discardRecovery();
      this.#quarantined = true;
      this.#publish({
        pending: false,
        uncertain: true,
        notice: "closure_unconfirmed",
      });
      return false;
    }
  }
  close = async () => {
    this.#cancelStart = true;
    this.#publish({ frameUrl: null });
    if (this.#recovery) await this.#recovery;
    await this.#flight;
    if (this.#disposed) return;
    if (!this.#token) {
      if (this.#quarantined) {
        this.#publish({ visible: true, pending: false, uncertain: true });
        return;
      }
      this.#dismiss();
      return;
    }
    if (await this.#stop()) this.#dismiss();
  };
  #dismiss() {
    this.#dismissed = true;
    this.#publish({ visible: false, pending: false });
  }
  navigate = () => this.close();
  setIdentity = async (identity: PreviewIdentity) => {
    if (identity.run === this.#identity.run && identity.source === this.#identity.source) return;
    const runChanged = identity.run !== this.#identity.run;
    this.#identity = { ...identity };
    ++this.#epoch;
    if (!this.#token) this.#clearSaved();
    this.#recovery = null;
    this.#recoveryChecked = false;
    await this.close();
    if (!this.#token && runChanged) {
      this.#observedCandidates.clear();
      this.#startDeadlineOff?.();
      this.#quarantined = false;
      this.#candidate = null;
      this.#id = null;
    }
    if (!this.#token && !this.#quarantined)
      this.#publish({ status: null, uncertain: false, notice: null });
  };
  dispose = () => {
    if (this.#disposed) return;
    this.#disposed = true;
    this.#cancelStart = true;
    ++this.#epoch;
    this.#cancelRenewal();
    this.#startDeadlineOff?.();
    for (const request of this.#requests) request.abort();
    const id = this.#id,
      token = this.#token;
    this.#token = null;
    this.#listeners.clear();
    if (id && token && !this.#closing && !this.#pendingStops.has(id))
      void this.#abandon({ preview_id: id, lease_token: token });
  };
}
