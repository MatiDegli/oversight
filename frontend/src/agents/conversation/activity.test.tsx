import { renderToReadableStream } from "react-dom/server";
import { expect, it } from "vitest";
import { ConversationPanel } from "./ConversationPanel";
import { progressState } from "./progressFixture";
import { selectConversation, type Conversation } from "./selectConversation";

async function markup(conversation: Conversation) {
  const stream = await renderToReadableStream(<ConversationPanel conversation={conversation} />);
  await stream.allReady;
  return new Response(stream).text();
}

async function html(moment: "working" | "retry" | "limits", unknown = false) {
  const state = { ...progressState(moment), spendUnknown: unknown };
  const conversation = selectConversation(state, "agent-1", []);
  if (!conversation) throw new Error("Missing agent");
  return (await markup(conversation)).replace(/<[^>]*>/g, "");
}
it("shows an open block, folded earlier calls and newest call last", async () => {
  const text = await html("working");
  expect(text).toContain("Working3 min 20 s");
  expect(text).toContain("6 earlier calls50 ms in tools");
  expect(text).toContain("edit_file · Edited pricing.pyok 6 ms");
  expect(text).not.toContain("Read orders.py");
  expect(text).toContain("Round 1 · proposed, then you asked for changes6 calls · 2 min 41 s");
});
it("shows retry and time notices, keeps Revising, and omits unknown amounts and meters", async () => {
  expect(await html("retry")).toContain("Waiting for model (retry 3/5)Rate limited · 429");
  expect(await html("retry")).toContain("Revising");
  expect(await html("limits")).toContain("8 min of 10 min · time limit for this round");
  expect(await html("limits")).toContain(
    "Budget at 80% · US$120.04 of US$150 · new Meta runs stop at 90%",
  );
  expect(await html("limits", true)).toContain("Budget at 80% · new Meta runs stop at 90%");
  expect(await html("limits", true)).not.toContain("US$120.04");
  for (const unknown of [false, true]) {
    const data = selectConversation(
      { ...progressState("limits"), spendUnknown: unknown },
      "agent-1",
      [],
    );
    if (!data) throw new Error("Missing conversation");
    expect((await markup(data)).match(/w-\[150px\]/g)).toHaveLength(unknown ? 1 : 2);
  }
});

it("caps observed steps at the real run limit, matches the Waiting pill and shows Paused", async () => {
  const state = progressState("retry"),
    agent = state.agents["agent-1"];
  if (!agent) throw new Error("Missing agent");
  state.agents["agent-1"] = { ...agent, state: "working", step: 99 };
  const data = selectConversation(state, "agent-1", []);
  if (!data) throw new Error("Missing conversation");
  const text = (await markup(data)).replace(/<[^>]*>/g, "");
  expect(text).toContain("Step 30 of 30");
  expect(text).toContain("Waiting");
  if (!data.progress.openRound) throw new Error("Missing round");
  data.progress.openRound.paused = true;
  const activity = data.entries.find((entry) => entry.kind === "activity");
  if (activity?.kind !== "activity") throw new Error("Missing activity");
  activity.round.paused = true;
  const paused = await markup(data);
  expect(paused).toContain("Paused");
  expect(paused).not.toContain("animate-spin");
});
