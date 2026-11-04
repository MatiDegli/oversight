import type { LeadView, PreviewStatusEvent } from "@contracts";
import { resolvePreviewReference, type State } from "../../state";
import { createPreviewController } from "./previewController";
import type { PreviewClock, PreviewIdentity, PreviewPort } from "./previewPort";
import { previewStartup } from "./previewStartup";
import { parsePreviewStatus } from "./previewTypes";
import {
  sameOwnershipCandidate,
  tabPreviewOwnership,
  type PreviewOwnershipStore,
} from "./previewOwnership";

export const previewClock: PreviewClock = {
  schedule(callback, delay) {
    const timer = setTimeout(callback, delay);
    return () => {
      clearTimeout(timer);
    };
  },
};
/** App-owned session host. Only activation grants slot intent; observation never does. */
export function createPreviewHost(
  port: PreviewPort,
  clock: PreviewClock,
  identity: PreviewIdentity,
  read: () => State,
  canOpen: () => boolean,
  now = () => Date.now(),
  ownership: PreviewOwnershipStore | undefined = tabPreviewOwnership(),
) {
  const controller = createPreviewController(port, clock, identity, ownership);
  let currentIdentity = identity;
  let open = false;
  let disposed = false;
  let closing = false;
  let closeRequested = false;
  let opening: Promise<void> | null = null;
  let refreshFlight: Promise<void> | null = null;
  let refreshAgain = false;
  let generation = 0;
  let clickedAt = 0;
  let targetAgent: string | undefined;
  let bootstrapStatus: PreviewStatusEvent | null = null;
  let identityChanging = false;
  const listeners = new Set<() => void>();
  const recover = () => {
    const state = read();
    if (
      disposed ||
      closing ||
      !canOpen() ||
      previewStartup().kind === "invalid" ||
      currentIdentity.source !== "live" ||
      !currentIdentity.run ||
      state.gap ||
      state.run?.run_id !== currentIdentity.run
    )
      return Promise.resolve();
    if (
      !Object.values(state.leadViews).some((events) =>
        events.some(
          (event) =>
            event.payload.view.kind === "preview" && event.payload.through_seq <= state.lastSeq,
        ),
      )
    )
      return Promise.resolve(); // Do not consume recovery during an incomplete bootstrap prefix.
    let record;
    try {
      record = ownership?.load(currentIdentity);
    } catch {
      return Promise.resolve();
    }
    if (!record) return Promise.resolve();
    if (!record.unloaded) return controller.recover(() => null);
    const getCandidate = () => {
      if (disposed || !canOpen() || previewStartup().kind === "invalid") return null;
      for (const events of Object.values(read().leadViews)) {
        for (const event of events) {
          const request = event.payload;
          if (
            request.view.kind !== "preview" ||
            request.view.agent_id !== record.candidate.agent_id
          )
            continue;
          const candidate = resolvePreviewReference(read(), request).candidate;
          if (candidate) return candidate;
        }
      }
      return null;
    };
    // An earlier preview can arrive before this record's suggestion or revision.
    const candidate = getCandidate();
    if (!candidate || candidate.revision < record.candidate.revision) return Promise.resolve();
    if (!sameOwnershipCandidate(record.candidate, candidate)) {
      try {
        ownership?.clear(record);
      } catch {
        /* Storage refusal never grants recovery authority. */
      }
      return Promise.resolve();
    }
    return controller.recover(getCandidate);
  };
  const publish = (next: boolean) => {
    if (open === next || disposed) return;
    open = next;
    for (const listener of listeners) listener();
  };
  const refresh = () => {
    if (!open || closing || disposed) return;
    refreshAgain = true;
    if (refreshFlight) return;
    const epoch = generation;
    refreshFlight = (async () => {
      await opening;
      while (refreshAgain && epoch === generation) {
        refreshAgain = false;
        await controller.refresh();
      }
    })().finally(() => {
      refreshFlight = null;
    });
  };
  const close = async () => {
    closing = true;
    closeRequested = true;
    ++generation;
    refreshAgain = false;
    // Remove the application immediately; keep closure feedback until Stop is verified.
    controllerHide();
    await controller.close();
    if (!controller.snapshot().visible) publish(false);
    closing = false;
  };
  // App's bootstrap and Chat selection also navigate. Hidden recovery has no slot
  // to dismiss; an explicit Close can still drain its private authority.
  const navigate = () => (open || !canOpen() ? close() : Promise.resolve());
  // The panel proxy masks the frame during navigation/Stop without changing controller evidence.
  let frameHidden = false;
  let cached = controller.snapshot();
  let observed = cached;
  let masked: typeof cached | undefined;
  const panelListeners = new Set<() => void>();
  const notifyPanel = () => {
    const view = controller.snapshot();
    if (view !== observed) {
      observed = view;
      masked = undefined;
    }
    const next =
      frameHidden && view.frameUrl !== null
        ? (masked ??= Object.freeze({ ...view, frameUrl: null }))
        : view;
    if (next !== cached) {
      cached = next;
      for (const listener of panelListeners) listener();
    }
    if (
      closeRequested &&
      !view.owned &&
      !view.uncertain &&
      !view.pending &&
      (view.status?.state === "stopped" || view.status?.state === "failed") &&
      view.status.reason !== "cleanup_unverified"
    )
      publish(false);
  };
  const controllerHide = () => {
    frameHidden = true;
    notifyPanel();
  };
  const unsubscribe = controller.subscribe(notifyPanel);
  return {
    controller: {
      ...controller,
      snapshot: () => cached,
      subscribe: (listener: () => void) => {
        panelListeners.add(listener);
        return () => {
          panelListeners.delete(listener);
        };
      },
      close,
      navigate,
    },
    clock,
    targetAgent: () => targetAgent,
    elapsed: () => Math.max(0, Math.floor((now() - clickedAt) / 1000)),
    snapshot: () => open,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    activate(request: LeadView) {
      if (
        disposed ||
        closing ||
        opening ||
        previewStartup().kind === "invalid" ||
        !canOpen() ||
        !resolvePreviewReference(read(), request).candidate
      )
        return;
      targetAgent = request.view.kind === "preview" ? request.view.agent_id : undefined;
      closeRequested = false;
      frameHidden = false;
      clickedAt = now();
      publish(true);
      const see = () =>
        controller.see(() => {
          if (disposed || closing || previewStartup().kind === "invalid" || !canOpen()) return null;
          return resolvePreviewReference(read(), request).candidate;
        });
      // Keep the synchronous start path for memory-only hosts and await tab recovery otherwise.
      opening = (ownership ? recover().then(see) : see()).finally(() => {
        opening = null;
      });
      return opening;
    },
    accept(event: PreviewStatusEvent) {
      const state = read();
      if (
        disposed ||
        state.gap ||
        event.seq !== state.lastSeq ||
        event.payload.run_id !== state.run?.run_id
      )
        return;
      if ((!currentIdentity.run || identityChanging) && currentIdentity.source === "live") {
        // The applied SSE prefix can outrun App's identity effect. Retain metadata only.
        bootstrapStatus = event;
        return;
      }
      if (event.payload.run_id !== currentIdentity.run) return;
      const status = parsePreviewStatus(event.payload);
      controller.observe(status);
      void recover();
      if (status.state === "ready") refresh();
    },
    close,
    navigate,
    async setIdentity(next: PreviewIdentity) {
      if (next.run === currentIdentity.run && next.source === currentIdentity.source) {
        await recover();
        return;
      }
      currentIdentity = next;
      identityChanging = true;
      ++generation;
      frameHidden = true;
      refreshAgain = false;
      notifyPanel();
      await controller.setIdentity(next);
      identityChanging = false;
      if (bootstrapStatus) {
        const state = read();
        if (
          next.source === "live" &&
          !state.gap &&
          next.run === state.run?.run_id &&
          bootstrapStatus.payload.run_id === next.run
        )
          controller.observe(bootstrapStatus.payload);
        bootstrapStatus = null;
      }
      if (!controller.snapshot().visible) publish(false);
      await recover();
    },
    dispose() {
      disposed = true;
      ++generation;
      refreshAgain = false;
      bootstrapStatus = null;
      unsubscribe();
      listeners.clear();
      panelListeners.clear();
      controller.dispose();
    },
  };
}
export type PreviewHost = ReturnType<typeof createPreviewHost>;
