import { expect, it, vi } from "vitest";

import { createLeadDraftStore } from "./leadDraftModel";
import { taskBoxStore } from "./taskBoxModel";
import { CommandError } from "../transport";

it("uses the backend default profile, guards duplicate starts and clears only on success", async () => {
  const store = createLeadDraftStore();
  store.getState().setDraft("  Coordinate validation  ");
  let finish = () => {};
  const send = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const first = store.getState().start(send);
  expect(store.getState().pending).toBe(true);
  expect(await store.getState().start(send)).toBe(false);
  expect(send).toHaveBeenCalledExactlyOnceWith({
    task: "Coordinate validation",
  });
  finish();
  expect(await first).toBe(true);
  expect(store.getState()).toMatchObject({ draft: "", pending: false, failure: null });
});
it.each([409, 503, "network"])("keeps the first task and explains a %s refusal", async (status) => {
  const store = createLeadDraftStore();
  store.getState().setDraft("Keep this task");
  await store
    .getState()
    .start(() =>
      Promise.reject(
        status === "network"
          ? new TypeError()
          : new CommandError(Number(status), "wrong_state", "Lead refused"),
      ),
    );
  expect(store.getState()).toMatchObject({ draft: "Keep this task", pending: false });
  expect(store.getState().failure).toBe(
    status === "network"
      ? "The request did not reach the server. Check the connection and try again."
      : status === 503
        ? "The server failed to start the lead: Lead refused"
        : "Lead refused",
  );
});
it("moves into an empty direct task and preserves both drafts if that box is occupied", () => {
  const store = createLeadDraftStore();
  taskBoxStore.setState({ draft: "", pending: false });
  store.getState().setDraft("Move this");
  store.getState().moveToTask();
  expect(taskBoxStore.getState().draft).toBe("Move this");
  expect(store.getState().draft).toBe("");
  store.getState().setDraft("Keep this");
  store.getState().moveToTask();
  expect(taskBoxStore.getState().draft).toBe("Move this");
  expect(store.getState().draft).toBe("Keep this");
  taskBoxStore.setState({ draft: "" });
});
