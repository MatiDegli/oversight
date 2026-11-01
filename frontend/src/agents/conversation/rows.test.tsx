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
import { ConversationPanelHeader } from "./ConversationPanelHeader";
import { ConversationPanel } from "./ConversationPanel";
import { Composer } from "./Composer";
import { sketchState } from "./fixture";
import { selectConversation, type ConversationHeader } from "./selectConversation";
import { TimelineEntry } from "./rows";

it("names the agent once in the header and keeps the task in the first timeline row", () => {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  const task = conversation.entries[0];
  if (task?.kind !== "task") throw new Error("Missing task");
  const header = renderToStaticMarkup(<ConversationPanelHeader header={conversation.header} />);
  expect(header.match(/<h2\b/g)).toHaveLength(1);
  expect(header).toMatch(/<h2[^>]*>Agent A<\/h2>/);
  expect(header).not.toContain(task.text);
  const panel = renderToStaticMarkup(<ConversationPanel conversation={conversation} />);
  expect(panel.match(/<h[1-6]\b/g)).toHaveLength(1);
  expect(panel.split(task.text)).toHaveLength(2);
  expect(panel).toContain("Task from you");
});

it.each([false, true])("shows a sentence and keeps the tool on hover (compact=%s)", (compact) => {
  const step = sketchState.steps["agent-1"]?.find((step) => step.tool === "edit_file");
  if (!step) throw new Error("Missing edit");
  const html = renderToStaticMarkup(
    <TimelineEntry
      compact={compact}
      changeId="change-1"
      onOpenDiff={() => undefined}
      entry={{ kind: "step", seq: 1, ts: undefined, step }}
    />,
  );
  const text = html.replace(/<[^>]*>/g, "");
  expect(text).toContain(step.summary);
  expect(text).not.toContain(step.tool);
  expect(text).toContain("View in diff");
  expect(html).toContain(`title="${step.tool}"`);
  expect(html).not.toContain("<code");
});

it.each([false, true])("falls back to the tool in body type (compact=%s)", (compact) => {
  const step = sketchState.steps["agent-1"]?.[0];
  if (!step) throw new Error("Missing step");
  const html = renderToStaticMarkup(
    <TimelineEntry
      compact={compact}
      changeId="change-1"
      entry={{ kind: "step", seq: 1, ts: undefined, step: { ...step, summary: "" } }}
    />,
  );
  expect(html.replace(/<[^>]*>/g, "")).toContain(step.tool);
  expect(html).toContain(`title="${step.tool}"`);
  expect(html).not.toMatch(/<code|font-mono[^>]*>list_files/);
});

beforeEach(() => {
  draftStore.setState({}, true);
});

function mountComposer(agentId = "agent-1", state: ConversationHeader["state"] = "working") {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  const props = {
    header: { ...conversation.header, agentId, state },
    onSend: vi.fn().mockResolvedValue(undefined),
    onPause: vi.fn().mockResolvedValue(undefined),
    onResume: vi.fn().mockResolvedValue(undefined),
  };
  const values: unknown[] = [],
    pauseValues: unknown[] = [];
  let pauseKey: ReactElement["key"];
  function render(pause = false) {
    hooks.active = { values, cursor: 0 };
    try {
      const tree = Composer(props);
      if (!pause) return tree;
      const control = find(tree, PauseControl) as ReactElement<Parameters<typeof PauseControl>[0]>;
      if (control.key !== pauseKey) pauseValues.length = 0;
      pauseKey = control.key;
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
  "aria-label"?: string;
  "aria-hidden"?: boolean | "true" | "false";
  value?: string;
  onClick?: () => void;
  onChange?: (event: { target: { value: string } }) => void;
};

// These controls use aria-label or text with aria-hidden icons; no labelledby references.
function accessibleName(tree: ReactNode): string {
  return Children.toArray(tree)
    .map((child) => {
      if (typeof child === "string" || typeof child === "number" || typeof child === "bigint")
        return String(child);
      if (!isValidElement<Interactive>(child)) return "";
      if (child.props["aria-hidden"] === true || child.props["aria-hidden"] === "true") return "";
      return child.props["aria-label"] ?? accessibleName(child.props.children);
    })
    .join("");
}

class MissingElement extends Error {}

function find(
  tree: ReactNode,
  type: string | typeof PauseControl,
  text?: string,
): ReactElement<Interactive> {
  for (const child of Children.toArray(tree)) {
    if (!isValidElement<Interactive>(child)) continue;
    if (child.type === type && (!text || accessibleName(child) === text)) return child;
    if (child.props.children) {
      try {
        return find(child.props.children, type, text);
      } catch (error) {
        if (!(error instanceof MissingElement)) throw error;
        /* Continue with siblings. */
      }
    }
  }
  throw new MissingElement(`Missing ${String(type)}`);
}

it("finds controls by accessible name, ignoring hidden icons and honoring labels", () => {
  const tree = (
    <div>
      <span>Other content</span>
      <button>
        <span aria-hidden="true">Icon</span>Pause
      </button>
      <button aria-label="Resume">Continue</button>
    </div>
  );
  expect(accessibleName(find(tree, "button", "Pause"))).toBe("Pause");
  expect(accessibleName(find(tree, "button", "Resume"))).toBe("Resume");
});

it("does not hide a rendering error as a missing element", () => {
  const tree = (
    <div>
      <span>{{ invalid: true } as unknown as ReactNode}</span>
      <button>Send</button>
    </div>
  );
  expect(() => find(tree, "button", "Send")).toThrow(/Objects are not valid as a React child/);
});

it.each([
  ["working", "Pause"],
  ["paused", "Resume"],
] as const)("exposes the exact accessible name %s / %s at rest", (state, name) => {
  const view = mountComposer("agent-1", state);
  expect(accessibleName(find(view.render(true), "button", name))).toBe(name);
});

it("uses the shared pause states and preserves no-argument command callbacks", async () => {
  const view = mountComposer();
  find(view.render(true), "button", "Pause").props.onClick?.();
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
  find(view.render(true), "button", "Pause").props.onClick?.();
  await vi.waitFor(() => {
    expect(renderToStaticMarkup(view.render(true))).toContain("Pausing after this step");
  });
  find(view.render(), "textarea").props.onChange?.({ target: { value: "Read pricing.py" } });
  find(view.render(), "button", "Send").props.onClick?.();
  expect(renderToStaticMarkup(view.render(true))).toContain("Pausing after this step");
});

it.each([
  ["working", "Pause", "onPause"],
  ["paused", "Resume", "onResume"],
] as const)("clears a rejected %s / %s before Send fails", async (state, name, callback) => {
  const view = mountComposer("agent-1", state);
  view.props[callback].mockRejectedValueOnce(new Error("offline"));
  find(view.render(true), "button", name).props.onClick?.();
  await vi.waitFor(() => {
    expect(renderToStaticMarkup(view.render(true))).toContain(`${name} did not reach the server.`);
  });
  view.props.onSend.mockRejectedValueOnce(new Error("offline"));
  find(view.render(), "textarea").props.onChange?.({ target: { value: "Read pricing.py" } });
  find(view.render(), "button", "Send").props.onClick?.();
  expect(renderToStaticMarkup(view.render(true))).not.toContain('role="alert"');
  await vi.waitFor(() => {
    expect(renderToStaticMarkup(view.render())).toContain("Send did not reach the server.");
  });
  expect(renderToStaticMarkup(view.render(true))).not.toContain('role="alert"');
  find(view.render(true), "button", name).props.onClick?.();
  await vi.waitFor(() => {
    expect(view.props[callback]).toHaveBeenCalledTimes(2);
    expect(renderToStaticMarkup(view.render())).not.toContain('role="alert"');
  });
});

it.each([true, false])(
  "keeps only one alert when concurrent requests fail, Pause first: %s",
  async (pauseFirst) => {
    const view = mountComposer();
    let failPause!: () => void, failSend!: () => void;
    view.props.onPause.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          failPause = () => {
            reject(new Error("offline"));
          };
        }),
    );
    view.props.onSend.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          failSend = () => {
            reject(new Error("offline"));
          };
        }),
    );
    find(view.render(true), "button", "Pause").props.onClick?.();
    find(view.render(), "textarea").props.onChange?.({ target: { value: "Read pricing.py" } });
    find(view.render(), "button", "Send").props.onClick?.();
    (pauseFirst ? failPause : failSend)();
    await vi.waitFor(() => {
      expect(renderToStaticMarkup(view.render(pauseFirst))).toContain('role="alert"');
    });
    (pauseFirst ? failSend : failPause)();
    await vi.waitFor(() => {
      const html = renderToStaticMarkup(view.render()) + renderToStaticMarkup(view.render(true));
      expect(html).toContain(`${pauseFirst ? "Send" : "Pause"} did not reach the server.`);
      expect(html.match(/role="alert"/g)).toHaveLength(1);
    });
  },
);

it.each([
  ["working", "Pause", "onPause"],
  ["paused", "Resume", "onResume"],
] as const)("clears a Send failure when %s / %s is used", async (state, name, callback) => {
  const view = mountComposer("agent-1", state);
  view.props.onSend.mockRejectedValueOnce(new Error("offline"));
  find(view.render(), "textarea").props.onChange?.({ target: { value: "Read pricing.py" } });
  find(view.render(), "button", "Send").props.onClick?.();
  await Promise.resolve();
  expect(renderToStaticMarkup(view.render())).toContain("Send did not reach the server.");
  find(view.render(true), "button", name).props.onClick?.();
  await Promise.resolve();
  expect(view.props[callback]).toHaveBeenCalledExactlyOnceWith();
  expect(renderToStaticMarkup(view.render())).not.toContain('role="alert"');
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

it.each([true, false])(
  "blocks a duplicate Send after remount until settlement: %s",
  async (success) => {
    const view = mountComposer();
    const text = "Read pricing.py";
    let settle!: () => void;
    view.props.onSend.mockImplementationOnce(
      () =>
        new Promise((resolve, reject) => {
          settle = () => {
            if (success) resolve(undefined);
            else reject(new Error("offline"));
          };
        }),
    );
    find(view.render(), "textarea").props.onChange?.({ target: { value: text } });
    const staleButton = find(view.render(), "button", "Send");
    staleButton.props.onClick?.();
    const remounted = mountComposer();
    remounted.props.onSend = view.props.onSend;
    const tree = remounted.render();
    find(tree, "button", "Send").props.onClick?.();
    staleButton.props.onClick?.();
    const calls = view.props.onSend.mock.calls.length;
    settle();
    await Promise.resolve();
    expect(calls).toBe(1);
    expect(find(tree, "button", "Send").props).toHaveProperty("aria-disabled", true);
    expect(find(tree, "textarea").props).toHaveProperty("readOnly", true);
    expect(find(remounted.render(), "textarea").props.value).toBe(success ? "" : text);
    expect(find(remounted.render(), "textarea").props).toHaveProperty("readOnly", false);
    if (!success) {
      find(remounted.render(), "button", "Send").props.onClick?.();
      await Promise.resolve();
      expect(view.props.onSend).toHaveBeenCalledTimes(2);
      expect(find(remounted.render(), "textarea").props.value).toBe("");
    }
  },
);

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
