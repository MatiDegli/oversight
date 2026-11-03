import { useContext } from "react";
import { renderToReadableStream } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
import { PreviewActionsContext, type PreviewActions } from "../../agents";
import { applyEvent, createInitialState } from "../../state";
import { Frame } from "../Frame";
import { InitialShellLayout } from "../shell/useShell";
import { initialView } from "../view";
import { chatEvents } from "./previewChat.browser.support";
import { previewStartup } from "./previewStartup";
import { startupFixture } from "./previewStartup.browser.support";

vi.mock("./previewStartup", () => ({ previewStartup: vi.fn() }));
const observed = vi.hoisted(() => [] as (PreviewActions | null)[]);
vi.mock("../shell/FocusPanel", async (original) => {
  const actual = await original<typeof import("../shell/FocusPanel")>();
  return {
    ...actual,
    FocusPanel(props: Parameters<typeof actual.FocusPanel>[0]) {
      observed.push(useContext(PreviewActionsContext));
      return <actual.FocusPanel {...props} />;
    },
  };
});
beforeEach(() => {
  observed.length = 0;
});

async function frame(layout: "Chat" | "Oversight" = "Chat", agentId = "agent-900001") {
  const state = chatEvents().reduce(applyEvent, createInitialState());
  const view = {
    ...initialView,
    activeTab: `conversation:${agentId}`,
    tabs: [{ ...initialView, id: `conversation:${agentId}`, conversation: agentId }],
  };
  const stream = await renderToReadableStream(
    <InitialShellLayout value={layout}>
      <Frame
        state={state}
        view={view}
        onNodeTap={vi.fn()}
        onClosePanel={vi.fn()}
        canOpenChange={() => false}
        onOpenChange={vi.fn()}
        onOpenSettings={vi.fn()}
        onMessageAgent={vi.fn()}
        onMessageLead={vi.fn()}
        previewActions={{ state, readState: () => state, onPreview: vi.fn(), onReview: vi.fn() }}
      />
    </InitialShellLayout>,
  );
  await stream.allReady;
  return new Response(stream).text();
}

it.each(["valid", "missing", "invalid"] as const)(
  "composes the %s frozen reader result into only the preview capability",
  async (kind) => {
    vi.mocked(previewStartup).mockReturnValue(
      kind === "valid" ? { kind, report: startupFixture } : { kind },
    );
    const html = await frame();
    const actions = observed.at(-1);
    expect(actions?.onPreview).toBeTypeOf("function");
    expect(actions?.onReview).toBeTypeOf("function");
    expect(Boolean(actions?.previewRestriction)).toBe(kind === "invalid");
    expect(html.match(/class="lead-preview-primary" disabled=""/gu) ?? []).toHaveLength(
      kind === "invalid" ? 1 : 0,
    );
    expect(html).toContain('class="lead-preview-secondary">Review change');
  },
);
