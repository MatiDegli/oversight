import { renderToReadableStream, renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { FileView } from "../../editor";
import { loadDecisionInbox } from "../../review";
import { createInitialState } from "../../state";
import { loadConversationPanel, type Conversation } from "../../agents";
import { ConversationFrame, FileFrame, InboxFrame } from "./PanelFallbacks";

vi.mock("../../editor/monaco", () => ({ monaco: {} }));
vi.mock("../../graph/firstDraw", async (importOriginal) => ({
  ...(await importOriginal()),
  whenFirstGraphDrawn: () => Promise.resolve(),
}));
// Simulate a future shared-header edit: both cold frames and loaded panels must inherit it.
vi.mock("../../editor/FileViewHeader", async (importOriginal) => ({
  ...(await importOriginal()),
  FileViewHeader: ({ target }: Pick<import("../../editor").FileViewProps, "target">) => (
    <header>{`Updated header for ${target.path}`}</header>
  ),
}));
vi.mock("../../review/InboxHeader", () => ({
  InboxViews: () => <nav>Updated views</nav>,
  InboxHeading: () => <header>Updated heading</header>,
}));

it.each(["working", "revising"] as const)(
  "shares the progress header between the conversation frame and loaded %s panel",
  async (status) => {
    const conversation: Conversation = {
      header: {
        agentId: "agent-1",
        changeId: "change-1",
        task: "Check the quantity",
        slot: "A",
        state: status,
        modelLabel: "Muse Spark",
        reasoning: null,
        step: 99,
        maxSteps: 30,
        waiting: status === "working",
        cost: undefined,
        round: 2,
        zonePaths: [],
      },
      entries: [],
      progress: {
        wait: undefined,
        openRound: undefined,
        limits: 600,
        budget: undefined,
        spendUnknown: false,
      },
    };
    const { ConversationPanel } = await loadConversationPanel();
    const frame = renderToStaticMarkup(<ConversationFrame header={conversation.header} />);
    const panel = renderToStaticMarkup(<ConversationPanel conversation={conversation} />);
    const header = frame.match(/<header[\s\S]*?<\/header>/)?.[0];
    expect(header).toBeDefined();
    expect(panel.match(/<header[\s\S]*?<\/header>/)?.[0]).toBe(header);
    expect(header?.replace(/<[^>]*>/g, "")).toContain("Step 30 of 30");
    expect(header).toContain(status === "working" ? "Waiting" : "Revising");
  },
);

it("propagates inbox header edits to both the loaded panel and its frame", async () => {
  const { DecisionInbox } = await loadDecisionInbox();
  for (const Panel of [InboxFrame, DecisionInbox]) {
    const html = renderToStaticMarkup(<Panel state={createInitialState()} />);
    expect(html).toContain("<nav>Updated views</nav>");
    expect(html).toContain("<header>Updated heading</header>");
  }
});

it("propagates file header edits to both the loaded panel and its frame", async () => {
  const files = {
    readOnly: true,
    drafts: new Map(),
    inFlightSaves: new Map(),
    saved: new Map(),
    readFile: vi.fn(),
    saveFile: vi.fn(),
  };
  for (const Panel of [FileFrame, FileView]) {
    const stream = await renderToReadableStream(
      <Panel
        target={{ path: "shop/cart.py", agentId: null, startLine: 1, endLine: 1 }}
        files={files}
        commit="head"
        onClose={() => {}}
      />,
    );
    await stream.allReady;
    const html = await new Response(stream).text();
    expect(html).toContain("<header>Updated header for shop/cart.py</header>");
  }
});
