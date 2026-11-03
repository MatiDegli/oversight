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
    tabs: [
      {
        ...initialView,
        id: `conversation:${agentId}`,
        conversation: agentId,
        frameIds: [],
        frameKey: "startup-fixture",
      },
    ],
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

it.each(["Chat", "Oversight"] as const)(
  "mounts the reused valid report only in the selected Chat composer (%s)",
  async (layout) => {
    vi.mocked(previewStartup).mockReturnValue({ kind: "valid", report: startupFixture });
    for (const agent of ["agent-1", "agent-900001"]) {
      const html = await frame(layout, agent);
      const composer = html.slice(html.indexOf('class="conversation-composer'));
      expect(html.includes('class="preview-startup"')).toBe(layout === "Chat");
      if (layout === "Chat") {
        expect(html.match(/>Preview startup<\/h2>/gu)).toHaveLength(1);
        expect(composer).toContain(startupFixture.reason);
        expect(composer.match(/<dt>/gu)).toHaveLength(10);
        expect(composer).toContain("Merge mode:");
        expect(composer).toContain("<textarea");
        expect(composer).toContain("Send");
        expect(composer).not.toMatch(/<details|autofocus|Dismiss/iu);
      }
    }
  },
);

it("associates invalid preview with the single reused explanation and unique heading", async () => {
  vi.mocked(previewStartup).mockReturnValue({ kind: "invalid" });
  for (const agent of ["agent-1", "agent-900001"]) {
    const html = await frame("Chat", agent);
    const description = observed.at(-1)?.previewRestriction?.describedBy;
    expect(description).toBeTypeOf("string");
    expect(
      html.match(/The preview startup report is invalid, so reviewed-app previews stay off\./gu),
    ).toHaveLength(1);
    expect(html).toContain(`id="${description ?? "missing"}"`);
    const heading = /<h2 id="([^"]+)">Preview startup<\/h2>/u.exec(html)?.[1];
    expect(heading).toBeTypeOf("string");
    expect(heading).not.toBe(description);
    expect(html).toContain(`aria-labelledby="${heading ?? "missing"}"`);
    expect(html.match(new RegExp(`id="${heading ?? "missing"}"`, "gu"))).toHaveLength(1);
    if (agent === "agent-900001")
      expect(html).toContain(`aria-describedby="${description ?? "missing"}"`);
  }
});

it("adds no missing disclosure, wrapper, text or spacing to either selected composer", async () => {
  vi.mocked(previewStartup).mockReturnValue({ kind: "missing" });
  for (const agent of ["agent-1", "agent-900001"]) {
    const html = await frame("Chat", agent);
    expect(html).not.toMatch(
      /preview-startup|max-h-\[45dvh\]|id="[^"]*-description"|Not enabled on this server/gu,
    );
    expect(html).toContain("Merge mode:");
    expect(html).toContain("<textarea");
  }
});

it("keeps the invalid gate outside Chat without referencing an unmounted disclosure", async () => {
  vi.mocked(previewStartup).mockReturnValue({ kind: "invalid" });
  const html = await frame("Oversight");
  expect(observed.at(-1)?.previewRestriction).toEqual({ describedBy: undefined });
  expect(html).not.toContain('class="preview-startup"');
  expect(html).toContain('class="lead-preview-primary" disabled=""');
  expect(html).toContain('class="lead-preview-secondary">Review change');
});
