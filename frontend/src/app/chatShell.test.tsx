import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { Frame } from "./Frame";
import { InitialShellLayout } from "./shell/useShell";
import { ChatAgentsFrame, ChatEmpty, ChatWorkFrame } from "./shell/ChatShell";
import { through } from "./replay.fixture";
import { modeEvent } from "./chatMerge.browser.fixture";
import { applyEvent } from "../state";
import { initialView } from "./view";

it("fills agents while retaining the empty work extension slot", () => {
  const html = renderToStaticMarkup(
    <>
      <ChatAgentsFrame state={through(2)} selectedAgentId="agent-1" onOpenConversation={vi.fn()} />
      <ChatWorkFrame />
    </>,
  );
  expect(html).toContain('aria-label="Agents"');
  expect(html).toContain('data-selected-agent="agent-1"');
  expect(html).toContain('aria-label="Where they work"');
  expect(html.match(/class="chat-side-body"><\/div>/g)).toHaveLength(1);
  expect(html).toContain(
    "No agents yet. Start a task and its agent appears here with its own color and letter.",
  );
  expect(html).toContain("New task");
  expect(html).toContain("New lead");
  expect(html).not.toMatch(/Nobody|article|avatar/);
});
it("opens in Chat by default with the real replay TaskBox and no graph", () => {
  const html = renderToStaticMarkup(
    <Frame
      state={through(2)}
      view={initialView}
      onNodeTap={vi.fn()}
      canOpenChange={() => false}
      onOpenChange={vi.fn()}
      onClosePanel={vi.fn()}
      replayOf="recorded run"
    />,
  );
  expect(html).toContain("shell-workspace chat-shell");
  expect(html).toMatch(/aria-pressed="true"[^>]*>.*Chat/);
  expect(html).toContain("Replay of a recorded run: agents cannot be started here.");
  expect(html).not.toContain("Loading repository graph");
  expect(html).not.toContain('aria-label="Repository graph"');
});

it.each(["Chat", "Oversight"] as const)(
  "offers the file task action only in Oversight (%s)",
  (layout) => {
    const html = renderToStaticMarkup(
      <InitialShellLayout value={layout}>
        <Frame
          state={through(14)}
          view={{
            ...initialView,
            selected: "shop.pricing:compute_price",
            file: { path: "shop/pricing.py", agentId: null, startLine: 14, endLine: 21 },
          }}
          files={{
            drafts: new Map(),
            inFlightSaves: new Map(),
            saved: new Map(),
            readFile: vi.fn(),
            saveFile: vi.fn(),
          }}
          onNodeTap={vi.fn()}
          canOpenChange={() => false}
          onOpenChange={vi.fn()}
          onClosePanel={vi.fn()}
          onStartAgent={vi.fn()}
        />
      </InitialShellLayout>,
    );
    expect(html.includes("Start an agent here")).toBe(layout === "Oversight");
  },
);

it("passes the selected agent and conversation callback to the C2 body slot", () => {
  const onOpenConversation = vi.fn();
  const body = vi.fn(() => null);
  renderToStaticMarkup(
    <ChatAgentsFrame
      state={through(2)}
      selectedAgentId="agent-1"
      onOpenConversation={onOpenConversation}
    >
      {body}
    </ChatAgentsFrame>,
  );
  expect(body).toHaveBeenCalledWith({ selectedAgentId: "agent-1", onOpenConversation });
});
it("promises approval only where the merge mode keeps it, in the start line and the box", () => {
  const start = (state: ReturnType<typeof through>) =>
    renderToStaticMarkup(<ChatEmpty state={state} onStart={vi.fn()} />);
  const approval = "nothing reaches main without your approval.";
  const rules =
    "a change that passes every merge rule merges by itself; the rest waits for your approval.";
  for (const mode of [null, "ask_me"] as const) {
    const state = mode ? applyEvent(through(2), modeEvent(3, mode)) : through(2);
    const html = start(state);
    expect(html).toContain("One agent does the task itself; you approve its change.");
    expect(html).toContain(`Each agent works in its own worktree, and ${approval}`);
    expect(html).not.toContain("merges by itself");
  }
  const safe = start(applyEvent(through(2), modeEvent(3, "merge_when_safe")));
  expect(safe).toContain(`One agent does the task itself; ${rules}`);
  expect(safe).toContain(`Each agent works in its own worktree, and ${rules}`);
  expect(safe).not.toContain("you approve its change");
  expect(safe).not.toContain(approval);
});
