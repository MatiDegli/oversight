import { beforeEach, expect, it } from "vitest";
import {
  beginSend,
  clearSentDraft,
  draftKey,
  draftStore,
  finishSend,
  setDraft,
  setDraftFailure,
} from "./drafts";

beforeEach(() => {
  draftStore.setState({}, true);
});

it("separates drafts by run and agent, including unknown runs", () => {
  const key = draftKey("run-1", "agent-1");
  setDraft(key, "Read pricing.py");
  expect(draftStore.getState()[key]).toEqual({ text: "Read pricing.py", pending: false });
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
  expect(draftStore.getState()).toEqual({
    [other]: { text: "Read inventory.py", pending: false },
  });
  setDraft(other, "");
  expect(draftStore.getState()).toEqual({});
});

it("keeps a newer draft when an earlier send resolves after remount", () => {
  const key = draftKey("run-1", "agent-1");
  setDraft(key, "Read inventory.py");
  clearSentDraft(key, "Read pricing.py");
  expect(draftStore.getState()[key]).toEqual({ text: "Read inventory.py", pending: false });
});

it("claims sends per run and agent and keeps the claim across draft updates", () => {
  const key = draftKey("run-1", "agent-1");
  setDraft(key, "Read pricing.py");
  expect(beginSend(key)).toBe(true);
  expect(beginSend(key)).toBe(false);
  for (const other of [draftKey("run-2", "agent-1"), draftKey("run-1", "agent-2")]) {
    expect(beginSend(other)).toBe(true);
    finishSend(other);
    expect(draftStore.getState()[other]).toBeUndefined();
  }
  setDraft(key, "Read inventory.py");
  clearSentDraft(key, "Read pricing.py");
  expect(draftStore.getState()[key]).toEqual({ text: "Read inventory.py", pending: true });
  expect(beginSend(key)).toBe(false);
  finishSend(key);
  expect(draftStore.getState()[key]).toEqual({ text: "Read inventory.py", pending: false });
  expect(beginSend(key)).toBe(true);
});

it("holds a send claim until settlement even when its draft is cleared", () => {
  const key = draftKey("run-1", "agent-1");
  setDraft(key, "Read pricing.py");
  beginSend(key);
  clearSentDraft(key, "Read pricing.py");
  expect(draftStore.getState()[key]).toEqual({ text: "", pending: true });
  expect(beginSend(key)).toBe(false);
  finishSend(key);
  expect(draftStore.getState()).toEqual({});
  expect(beginSend(key)).toBe(true);
});

it("retains keyed failures through settlement and edits until retry, success or clearing", () => {
  const key = draftKey("run-1", "agent-1");
  const failure = "Send did not reach the server. Check the connection and try again.";
  setDraft(key, "Read pricing.py");
  expect(beginSend(key)).toBe(true);
  setDraftFailure(key, failure);
  expect(beginSend(key)).toBe(false);
  expect(draftStore.getState()[key]?.failure).toBe(failure);
  finishSend(key);
  expect(draftStore.getState()[key]).toEqual({
    text: "Read pricing.py",
    pending: false,
    failure,
  });
  for (const other of [
    draftKey("run-1", "agent-2"),
    draftKey("run-2", "agent-1"),
    draftKey(null, "agent-1"),
  ]) {
    expect(draftStore.getState()[other]).toBeUndefined();
    setDraft(other, "Other draft");
    expect(beginSend(other)).toBe(true);
    clearSentDraft(other, "Other draft");
    finishSend(other);
    expect(draftStore.getState()[other]).toBeUndefined();
    expect(draftStore.getState()[key]?.failure).toBe(failure);
  }
  setDraft(key, "Read inventory.py");
  expect(draftStore.getState()[key]?.failure).toBe(failure);
  expect(beginSend(key)).toBe(true);
  expect(draftStore.getState()[key]?.failure).toBeUndefined();
  setDraftFailure(key, failure);
  clearSentDraft(key, "Read pricing.py");
  finishSend(key);
  expect(draftStore.getState()[key]).toEqual({ text: "Read inventory.py", pending: false });
  setDraftFailure(key, failure);
  setDraft(key, "");
  finishSend(key);
  expect(draftStore.getState()[key]).toEqual({ text: "", pending: false, failure });
  setDraftFailure(key, undefined);
  expect(draftStore.getState()[key]).toBeUndefined();
});
