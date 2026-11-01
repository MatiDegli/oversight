import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
import { PauseControl } from "../PauseControl";
import { draftStore } from "./drafts";

const hooks = vi.hoisted(() => ({ active: null as null | { values: unknown[]; cursor: number } }));
vi.mock("react", async (original) => {
  const react = await original<typeof import("react")>();
  return {
    ...react,
    useState: <T,>(initial: T): [T, (next: T | ((value: T) => T)) => void] => {
      if (!hooks.active) return react.useState(initial);
      const { values } = hooks.active;
      const index = hooks.active.cursor++;
      if (!(index in values)) values[index] = initial;
      return [
        values[index] as T,
        (next: T | ((value: T) => T)) => {
          values[index] =
            typeof next === "function" ? (next as (value: T) => T)(values[index] as T) : next;
        },
      ];
    },
    useRef: <T,>(value: T) => {
      if (!hooks.active) return react.useRef(value);
      const { values } = hooks.active;
      const index = hooks.active.cursor++;
      if (!(index in values)) values[index] = { current: value };
      return values[index] as { current: T };
    },
    useId: () => (hooks.active ? "caption" : react.useId()),
  };
});
vi.mock("zustand", async (original) => {
  const zustand = await original<typeof import("zustand")>();
  return {
    ...zustand,
    useStore: (
      store: Parameters<typeof zustand.useStore>[0],
      select: (value: unknown) => unknown,
    ) => (hooks.active ? select(store.getState()) : zustand.useStore(store, select)),
  };
});
import { ConversationPanel } from "./ConversationPanel";
import { Composer } from "./Composer";
import { sketchState } from "./fixture";
import { selectConversation } from "./selectConversation";
import { TimelineEntry } from "./rows";

beforeEach(() => {
  draftStore.setState({}, true);
});

function mountComposer(agentId = "agent-1") {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  const props = {
    header: { ...conversation.header, agentId, state: "working" as const },
    onSend: vi.fn().mockResolvedValue(undefined),
    onPause: vi.fn().mockResolvedValue(undefined),
    onResume: vi.fn().mockResolvedValue(undefined),
  };
  const values: unknown[] = [],
    pauseValues: unknown[] = [];
  function render(pause = false) {
    hooks.active = { values, cursor: 0 };
    try {
      const tree = Composer(props);
      if (!pause) return tree;
      const control = find(tree, PauseControl) as ReactElement<Parameters<typeof PauseControl>[0]>;
      hooks.active = { values: pauseValues, cursor: 0 };
      return PauseControl(control.props);
    } finally {
      hooks.active = null;
    }
  }
  return { props, render };
}

type Interactive = {
  children?: ReactNode;
  value?: string;
  onClick?: () => void;
  onChange?: (event: { target: { value: string } }) => void;
};
function find(
  tree: ReactNode,
  type: string | typeof PauseControl,
  text?: string,
): ReactElement<Interactive> {
  for (const child of Children.toArray(tree)) {
    if (!isValidElement<Interactive>(child)) continue;
    if (child.type === type && (!text || child.props.children === text)) return child;
    if (child.props.children) {
      try {
        return find(child.props.children, type, text);
      } catch {
        /* Continue with siblings. */
      }
    }
  }
  throw new Error(`Missing ${String(type)}`);
}

it("uses the shared pause states and preserves no-argument command callbacks", async () => {
  const view = mountComposer();
  expect(renderToStaticMarkup(view.render(true))).toContain("Pause</button>");
  find(view.render(true), "button").props.onClick?.();
  await Promise.resolve();
  expect(view.props.onPause).toHaveBeenCalledExactlyOnceWith();
  expect(renderToStaticMarkup(view.render(true))).toContain("Pausing after this step");
});

it("shows a shared pause failure and permits retry", async () => {
  const view = mountComposer();
  view.props.onPause.mockRejectedValueOnce(new Error("offline"));
  find(view.render(true), "button").props.onClick?.();
  await Promise.resolve();
  const html = renderToStaticMarkup(view.render(true));
  expect(html).toContain('role="alert"');
  expect(html).toContain("Pause did not reach the server. Check the connection and try again.");
  expect(html).toContain('aria-disabled="false"');
});

it("restores a draft after remount only for the same agent", () => {
  find(mountComposer().render(), "textarea").props.onChange?.({
    target: { value: "Read pricing.py" },
  });
  expect(find(mountComposer().render(), "textarea").props.value).toBe("Read pricing.py");
  expect(find(mountComposer("agent-2").render(), "textarea").props.value).toBe("");
});

it.each([true, false])("clears the draft only when sending succeeds: %s", async (success) => {
  const view = mountComposer();
  if (!success) view.props.onSend.mockRejectedValueOnce(new Error("offline"));
  find(view.render(), "textarea").props.onChange?.({ target: { value: "Read pricing.py" } });
  find(view.render(), "button", "Send").props.onClick?.();
  await Promise.resolve();
  expect(view.props.onSend).toHaveBeenCalledExactlyOnceWith("Read pricing.py");
  expect(find(mountComposer().render(), "textarea").props.value).toBe(
    success ? "" : "Read pricing.py",
  );
});

it("keeps multilingual drafts intact and blocks repeated sends while pending", async () => {
  const view = mountComposer();
  const text = "راجع السعر 👩🏽‍💻\n检查价格 — café\n".repeat(100);
  let rejectSend!: (error: Error) => void;
  view.props.onSend.mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        rejectSend = reject;
      }),
  );
  find(view.render(), "textarea").props.onChange?.({ target: { value: text } });
  find(view.render(), "button", "Send").props.onClick?.();
  for (let attempt = 0; attempt < 10; attempt++) {
    const tree = view.render();
    expect(renderToStaticMarkup(tree)).toContain('readOnly=""');
    expect(find(tree, "button", "Send").props).toHaveProperty("aria-disabled", true);
    find(tree, "button", "Send").props.onClick?.();
  }
  expect(view.props.onSend).toHaveBeenCalledExactlyOnceWith(text);
  rejectSend(new Error("offline"));
  await Promise.resolve();
  expect(renderToStaticMarkup(view.render())).toContain(
    "Send did not reach the server. Check the connection and try again.",
  );
  expect(find(mountComposer().render(), "textarea").props.value).toBe(text);
  expect(find(view.render(), "button", "Send").props).toHaveProperty("aria-disabled", false);
});

it("keeps a positive cost below half a cent visible in the header chip", () => {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  conversation.header.cost = 0.001;
  expect(renderToStaticMarkup(<ConversationPanel conversation={conversation} />)).toContain(
    ">&lt;US$0.01</span>",
  );
});

it("renders optional header chips from retained facts and withholds message text", () => {
  const conversation = selectConversation(sketchState, "agent-1", [
    { seq: 14, ts: "2026-10-27T17:02:02Z", text: null },
  ]);
  if (!conversation) throw new Error("Missing fixture");
  conversation.header = {
    ...conversation.header,
    reasoning: null,
    cost: null,
    zonePaths: ["pricing.py", "cart.py", "orders.py"],
  };
  const html = renderToStaticMarkup(<ConversationPanel conversation={conversation} />);
  expect(html).toContain("Zone: pricing.py and 2 more");
  expect(html).toContain("Cost unknown");
  expect(html).not.toContain("reasoning");
  expect(html).toContain("Message sent. Its text is not in this recording.");
  conversation.header = { ...conversation.header, cost: undefined, round: 1, zonePaths: [] };
  const empty = renderToStaticMarkup(<ConversationPanel conversation={conversation} />);
  expect(empty).not.toMatch(/Cost unknown|US\$|Zone:|Round 1/);
});

it("renders failed and skipped test counts, case details and server errors without raw output", () => {
  const step = sketchState.steps["agent-1"]?.find((step) => step.tests);
  if (!step?.tests) throw new Error("Missing tests");
  const html = renderToStaticMarkup(
    <TimelineEntry
      changeId="change-1"
      entry={{
        kind: "step",
        seq: 7,
        ts: undefined,
        step: {
          ...step,
          result: "error",
          error: "Tests failed",
          tests: {
            ...step.tests,
            outcome: "failed",
            passed: 1,
            failed: 1,
            skipped: 1,
            failed_tests: ["tests/test_pricing.py::test_coupon"],
            cases: [
              {
                test_id: "tests/test_pricing.py::test_coupon",
                outcome: "failed",
                detail: "Expected 10 cents",
              },
              { test_id: "tests/test_pricing.py::test_price", outcome: "passed", detail: null },
              { test_id: "tests/test_pricing.py::test_tax", outcome: "skipped", detail: null },
            ],
            cases_complete: true,
          },
        },
      }}
    />,
  );
  expect(html).toContain("1 passed in 0.40s, 1 failed, 1 skipped");
  expect(html).toContain("tests/test_pricing.py::test_coupon · Expected 10 cents");
  expect(html).toContain("tests/test_pricing.py · 1 failed");
  expect(html).toContain("Tests failed");
  expect(html).not.toContain("View in diff");
});

it.each([false, null, undefined])(
  "omits per-file totals when case completeness is %s but keeps failure details",
  (complete) => {
    const step = sketchState.steps["agent-1"]?.find((step) => step.tests);
    if (!step?.tests) throw new Error("Missing tests");
    const tests = { ...step.tests };
    if (complete === undefined) Reflect.deleteProperty(tests, "cases_complete");
    else tests.cases_complete = complete;
    const html = renderToStaticMarkup(
      <TimelineEntry
        changeId="change-1"
        entry={{
          kind: "step",
          seq: 7,
          ts: undefined,
          step: {
            ...step,
            tests: {
              ...tests,
              outcome: "failed",
              failed: 1,
              failed_tests: ["tests/test_pricing.py::test_coupon"],
              cases: [
                ...(tests.cases ?? []),
                {
                  test_id: "tests/test_pricing.py::test_coupon",
                  outcome: "failed",
                  detail: "Expected 10 cents",
                },
              ],
            },
          },
        }}
      />,
    );
    expect(html).toContain("28 passed in 0.40s, 1 failed");
    expect(html).toContain("tests/test_pricing.py::test_coupon · Expected 10 cents");
    expect(html).not.toContain("tests/test_pricing.py ·");
  },
);

it.each([
  ["working", "onPause", "Pause"],
  ["revising", "onPause", "Pause"],
  ["paused", "onResume", "Resume"],
  ["working", "onStop", "Stop and discard"],
  ["revising", "onStop", "Stop and discard"],
  ["paused", "onStop", "Stop and discard"],
] as const)("keeps %s controls truthful with only %s", (state, callback, label) => {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  const html = renderToStaticMarkup(
    <Composer
      header={{ ...conversation.header, state }}
      {...{ [callback]: () => Promise.resolve() }}
    />,
  );
  expect(html).toContain(label);
  expect(html).not.toContain("Agent A is not running.");
  expect(html).not.toContain("<textarea");
  expect(html).not.toContain(">Send<");
  expect(html).not.toContain("Recorded run:");
});

it("explains an inactive agent's disabled message box", () => {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  const html = renderToStaticMarkup(
    <Composer
      header={{ ...conversation.header, state: "ready_for_review" }}
      onSend={() => Promise.resolve()}
    />,
  );
  expect(html).toContain('aria-disabled="true"');
  expect(html).toContain('readOnly=""');
  expect(html).toContain("Agent A is not running.");
  const captionId = /aria-describedby="([^"]+)"/.exec(html)?.[1];
  expect(captionId).toBeDefined();
  expect(html).toContain(`id="${captionId ?? ""}"`);
});
