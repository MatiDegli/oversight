import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { Frame } from "./Frame";
import { InitialShellLayout } from "./shell/useShell";
import { ChatAgentsFrame, ChatWorkFrame } from "./shell/ChatShell";
import { through } from "./replay.fixture";
import { initialView } from "./view";

it("offers empty extension slots without inventing inactivity or compact agents", () => {
  const html = renderToStaticMarkup(
    <>
      <ChatAgentsFrame selectedAgentId="agent-1" onOpenConversation={vi.fn()} />
      <ChatWorkFrame />
    </>,
  );
  expect(html).toContain('aria-label="Agents"');
  expect(html).toContain('data-selected-agent="agent-1"');
  expect(html).toContain('aria-label="Where they work"');
  expect(html.match(/class="chat-side-body"><\/div>/g)).toHaveLength(2);
  expect(html).not.toMatch(/Nobody|No agents|New task|article|avatar/);
});
it("initializes Chat only through the fixture context, with the real replay TaskBox", () => {
  const html = renderToStaticMarkup(
    <InitialShellLayout value="Chat">
      <Frame
        state={through(2)}
        view={initialView}
        onNodeTap={vi.fn()}
        canOpenChange={() => false}
        onOpenChange={vi.fn()}
        onClosePanel={vi.fn()}
        replayOf="recorded run"
      />
    </InitialShellLayout>,
  );
  expect(html).toContain("shell-workspace chat-shell");
  expect(html).toContain("Replay of a recorded run: agents cannot be started here.");
  expect(html).not.toContain("Loading repository graph");
  expect(html).not.toContain('aria-label="Repository graph"');
});
