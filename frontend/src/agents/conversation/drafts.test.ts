import { beforeEach, expect, it } from "vitest";
import { clearSentDraft, draftKey, draftStore, setDraft } from "./drafts";

beforeEach(() => {
  draftStore.setState({}, true);
});

it("separates drafts by run and agent, including unknown runs", () => {
  const key = draftKey("run-1", "agent-1");
  setDraft(key, "Read pricing.py");
  expect(draftStore.getState()[key]).toBe("Read pricing.py");
  for (const other of [
    draftKey("run-2", "agent-1"),
    draftKey("run-1", "agent-2"),
    draftKey(null, "agent-1"),
  ]) {
    expect(draftStore.getState()[other]).toBeUndefined();
  }
});

it("removes empty and successfully sent drafts while preserving other agents", () => {
  const key = draftKey("run-1", "agent-1"),
    other = draftKey("run-1", "agent-2");
  setDraft(key, "Read pricing.py");
  setDraft(other, "Read inventory.py");
  clearSentDraft(key, "Read pricing.py");
  expect(draftStore.getState()).toEqual({ [other]: "Read inventory.py" });
  setDraft(other, "");
  expect(draftStore.getState()).toEqual({});
});

it("keeps a newer draft when an earlier send resolves after remount", () => {
  const key = draftKey("run-1", "agent-1");
  setDraft(key, "Read inventory.py");
  clearSentDraft(key, "Read pricing.py");
  expect(draftStore.getState()[key]).toBe("Read inventory.py");
});
