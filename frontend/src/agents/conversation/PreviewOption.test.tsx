import type { LeadViewEvent, ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import fixture from "../../../../fixtures/views/reference-cases.json";
import { applyEvent, createInitialState } from "../../state";
import type { ReactElement, ReactNode } from "react";
import { PreviewActionsContext, type PreviewActions } from "./PreviewActionsContext";
import { PreviewOption } from "./PreviewOption";
const context = vi.hoisted(() => ({ current: undefined as PreviewActions | undefined }));
vi.mock("react", async (original) => {
  const actual = await original<typeof import("react")>();
  return {
    ...actual,
    useContext: (value: typeof PreviewActionsContext) =>
      context.current ?? actual.useContext(value),
  };
});
function setup() {
  const state = (fixture.events as ServerEvent[])
    .slice(0, 6)
    .reduce(applyEvent, createInitialState());
  const agent = state.agents["agent-1"];
  if (!agent) throw new Error("Missing agent");
  state.agents[agent.agent_id] = { ...agent, state: "ready_for_review" };
  state.decisionEvents.push({
    type: "agent.status",
    seq: 7,
    ts: "2026-11-03T00:00:00Z",
    payload: {
      ...agent,
      state: "ready_for_review",
      change_id: state.agents[agent.agent_id]?.change_id ?? agent.change_id,
    },
  });
  const event: LeadViewEvent = {
    type: "lead.view",
    seq: 8,
    ts: "same",
    payload: {
      agent_id: "lead",
      through_seq: 7,
      caption: "<script>caption</script>",
      view: { kind: "preview", agent_id: agent.agent_id },
    },
  };
  state.leadViews.lead = [event];
  state.lastSeq = 8;
  return { state, event };
}
it("uses team text/label, escapes subordinate speech, and offers both capabilities", () => {
  const { state, event } = setup();
  const html = renderToStaticMarkup(
    <PreviewActionsContext
      value={{ state, readState: () => state, onPreview: () => {}, onReview: () => {} }}
    >
      <PreviewOption state={state} event={event} />
    </PreviewActionsContext>,
  );
  expect(html).toContain("Agent A");
  expect(html).toContain("--slot-text:var(--agent-a-text)");
  expect(html).not.toContain("agent-1");
  expect(html).toContain("&lt;script&gt;caption&lt;/script&gt;");
  expect(html).toContain('class="lead-preview-primary"');
  expect(html).toContain('class="lead-preview-secondary"');
  expect(html).not.toContain('disabled=""');
});
it.each([true, false])(
  "disables both buttons in replay or a missing-capability session (%s)",
  (replay) => {
    const { state, event } = setup();
    const html = renderToStaticMarkup(
      <PreviewOption state={state} event={event} replay={replay} />,
    );
    expect(html.match(/disabled=""/g)).toHaveLength(2);
    expect(html).toContain(replay ? "unavailable in replay" : "unavailable in this session");
  },
);
it("shows one reason and no actions for a stale suggestion", () => {
  const { state, event } = setup();
  state.leadViews = {};
  const html = renderToStaticMarkup(<PreviewOption state={state} event={event} />);
  expect(html).toContain("unavailable in this run");
  expect(html).not.toContain("<button");
});

it("withholds both capabilities for historical Chat retained from a live session", () => {
  const { state, event } = setup();
  const html = renderToStaticMarkup(
    <PreviewActionsContext
      value={{
        state,
        readState: () => state,
        readOnlyReason: "Historical view: preview and review are unavailable.",
        onPreview: () => {
          throw new Error("inert");
        },
        onReview: () => {
          throw new Error("inert");
        },
      }}
    >
      <PreviewOption state={state} event={event} />
    </PreviewActionsContext>,
  );
  expect(html.match(/disabled=""/g)).toHaveLength(2);
  expect(html).toContain("Historical view");
});

it.each(["valid", "missing", "invalid"] as const)(
  "restricts only preview for %s startup through the context seam",
  (kind) => {
    const { state, event } = setup();
    const html = renderToStaticMarkup(
      <PreviewActionsContext
        value={{
          state,
          readState: () => state,
          onPreview: vi.fn(),
          onReview: vi.fn(),
          previewRestriction:
            kind === "invalid" ? { describedBy: "startup-description" } : undefined,
        }}
      >
        <PreviewOption state={state} event={event} />
      </PreviewActionsContext>,
    );
    expect(html.match(/disabled=""/g) ?? []).toHaveLength(kind === "invalid" ? 1 : 0);
    expect(html.includes('aria-describedby="startup-description"')).toBe(kind === "invalid");
    expect(html).not.toContain("The preview startup report");
    expect(html).toContain('class="lead-preview-secondary">Review change');
  },
);

type ButtonProps = {
  children?: ReactNode;
  onClick?: (event: { currentTarget: HTMLButtonElement }) => void;
};
function buttons(node: ReactNode): ReactElement<ButtonProps>[] {
  if (Array.isArray(node)) return node.flatMap((child: ReactNode) => buttons(child));
  if (!node || typeof node !== "object" || !("props" in node)) return [];
  const element = node as ReactElement<ButtonProps>;
  return element.type === "button" ? [element] : buttons(element.props.children);
}
it("guards a direct handler call for invalid startup while Review still resolves fresh evidence", () => {
  const { state, event } = setup();
  const onPreview = vi.fn(),
    onReview = vi.fn(),
    readState = vi.fn(() => state);
  context.current = {
    state,
    readState,
    onPreview,
    onReview,
    previewRestriction: { describedBy: "startup-description" },
  };
  try {
    const [preview, review] = buttons(PreviewOption({ state, event }));
    const click = { currentTarget: {} as HTMLButtonElement };
    preview?.props.onClick?.(click);
    expect(onPreview).not.toHaveBeenCalled();
    expect(readState).not.toHaveBeenCalled();
    review?.props.onClick?.(click);
    expect(readState).toHaveBeenCalledOnce();
    expect(onReview).toHaveBeenCalledOnce();
    state.leadViews = {};
    review?.props.onClick?.(click);
    expect(onReview).toHaveBeenCalledOnce();
  } finally {
    context.current = undefined;
  }
});
