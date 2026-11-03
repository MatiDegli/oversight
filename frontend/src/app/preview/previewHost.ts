import type { LeadView, PreviewStatusEvent } from "@contracts";
import { resolvePreviewReference, type State } from "../../state";
import { createPreviewController } from "./previewController";
import type { PreviewClock, PreviewIdentity, PreviewPort } from "./previewPort";
import { parsePreviewStatus } from "./previewTypes";

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
) {
  const controller = createPreviewController(port, clock, identity);
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
  const listeners = new Set<() => void>();
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
  // The panel proxy masks the frame during navigation/Stop without changing controller evidence.
  let frameHidden = false;
  let cached = controller.snapshot();
  const panelListeners = new Set<() => void>();
  const notifyPanel = () => {
    const view = controller.snapshot();
    cached = frameHidden ? Object.freeze({ ...view, frameUrl: null }) : view;
    for (const listener of panelListeners) listener();
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
      navigate: close,
    },
    clock,
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
        !canOpen() ||
        !resolvePreviewReference(read(), request).candidate
      )
        return;
      closeRequested = false;
      frameHidden = false;
      clickedAt = now();
      publish(true);
      opening = controller
        .see(() => {
          if (disposed || closing || !canOpen()) return null;
          return resolvePreviewReference(read(), request).candidate;
        })
        .finally(() => {
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
        event.payload.run_id !== currentIdentity.run ||
        state.run?.run_id !== currentIdentity.run
      )
        return;
      const status = parsePreviewStatus(event.payload);
      controller.observe(status);
      if (status.state === "ready") refresh();
    },
    close,
    navigate: close,
    async setIdentity(next: PreviewIdentity) {
      if (next.run === currentIdentity.run && next.source === currentIdentity.source) return;
      currentIdentity = next;
      ++generation;
      frameHidden = true;
      refreshAgain = false;
      notifyPanel();
      await controller.setIdentity(next);
      if (!controller.snapshot().visible) publish(false);
    },
    dispose() {
      disposed = true;
      ++generation;
      refreshAgain = false;
      unsubscribe();
      listeners.clear();
      panelListeners.clear();
      controller.dispose();
    },
  };
}
export type PreviewHost = ReturnType<typeof createPreviewHost>;
