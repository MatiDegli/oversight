import { expect, it, vi } from "vitest";
import type { PreviewStatusEvent } from "@contracts";
const seam = vi.hoisted(() => ({
  callback: undefined as ((event: PreviewStatusEvent) => void) | undefined,
}));
vi.mock("../transport", () => ({
  startAppTransport: (
    _store: unknown,
    _error: unknown,
    _open: unknown,
    _replay: unknown,
    callback: (event: PreviewStatusEvent) => void,
  ) => {
    seam.callback = callback;
    return { ready: Promise.resolve(), stop: vi.fn() };
  },
}));
vi.mock("../editor/loader", () => ({ editorReady: Promise.resolve() }));
vi.mock("../editor", () => ({ prefetchEditorWhenIdle: () => () => {} }));
import { startAppSession } from "./session";
it("revokes obsolete lifecycle callbacks before stopping the session", () => {
  const accepted = vi.fn();
  const stop = startAppSession(null, vi.fn(), undefined, undefined, accepted);
  const event = { type: "preview.status" } as PreviewStatusEvent;
  seam.callback?.(event);
  expect(accepted).toHaveBeenCalledOnce();
  stop();
  seam.callback?.(event);
  expect(accepted).toHaveBeenCalledOnce();
});
