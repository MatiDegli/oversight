import { renderToReadableStream, renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { FileView } from "../../editor";
import { loadDecisionInbox } from "../../review";
import { createInitialState } from "../../state";
import { FileFrame, InboxFrame } from "./PanelFallbacks";

vi.mock("../../editor/monaco", () => ({ monaco: {} }));
vi.mock("../../graph", async (importOriginal) => ({
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
