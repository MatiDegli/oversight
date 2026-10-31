import type { AgentStatus } from "@contracts";
import type { ReactElement, SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";

const hooks: { value: unknown } = vi.hoisted(() => ({ value: undefined }));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useState: <T,>(initial: T) => {
    if (hooks.value === undefined) hooks.value = initial;
    return [
      hooks.value as T,
      (next: SetStateAction<T>) => {
        hooks.value =
          typeof next === "function" ? (next as (prev: T) => T)(hooks.value as T) : next;
      },
    ];
  },
}));
import { PauseControl } from "./PauseControl";

const onPause = vi.fn<(id: string) => Promise<unknown>>();
const onResume = vi.fn<(id: string) => Promise<unknown>>();
beforeEach(() => {
  hooks.value = undefined;
  onPause.mockReset().mockResolvedValue(undefined);
  onResume.mockReset().mockResolvedValue(undefined);
});

function render(state: AgentStatus["state"] = "working") {
  let previous: unknown;
  let tree: ReturnType<typeof PauseControl>;
  do {
    previous = hooks.value;
    tree = PauseControl({ agentId: "agent-1", state, onPause, onResume });
  } while (previous !== hooks.value);
  const element = tree as ReactElement<{
    children: ReactElement<{ onClick: () => void }>[];
  }> | null;
  return {
    html: renderToStaticMarkup(tree),
    click: async () => {
      element?.props.children[0]?.props.onClick();
      await Promise.resolve();
    },
  };
}

it.each([
  ["working", "Pause"],
  ["revising", "Pause"],
  ["paused", "Resume"],
  ["starting", null],
  ["ready_for_review", null],
  ["halted", null],
  ["stopped", null],
  ["done", null],
] as const)("renders %s as %s", (state, label) => {
  const { html } = render(state);
  if (label === null) expect(html).toBe("");
  else {
    expect(html).toContain(`${label}</button>`);
    expect(html).toContain('aria-disabled="false"');
    expect(html).not.toMatch(/ disabled=/);
  }
});

it.each(["working", "revising"] as const)(
  "waits for a state change after pausing %s",
  async (state) => {
    let finish!: () => void;
    onPause.mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    const pending = render(state).click();
    expect(render(state).html).toContain('aria-disabled="true"');
    await render(state).click();
    expect(onPause).toHaveBeenCalledExactlyOnceWith("agent-1");
    finish();
    await pending;
    expect(render(state).html).toContain("Pausing after this step");
    expect(render(state).html).toContain('aria-disabled="true"');
    await render(state).click();
    expect(onPause).toHaveBeenCalledTimes(1);
    expect(render("paused").html).toContain("Resume</button>");
    expect(render("working").html).toContain('aria-disabled="false"');
  },
);

it.each(["working", "paused"] as const)(
  "shows a failure and permits retry from %s",
  async (state) => {
    const command = state === "paused" ? onResume : onPause;
    const action = state === "paused" ? "Resume" : "Pause";
    command.mockRejectedValueOnce(new Error("offline"));
    await render(state).click();
    const { html } = render(state);
    expect(html).toContain('role="alert"');
    expect(html).toContain(
      `${action} did not reach the server. Check the connection and try again.`,
    );
    expect(html).toContain(`${action}</button>`);
    expect(html).toContain('aria-disabled="false"');
    await render(state).click();
    expect(render(state).html).not.toContain('role="alert"');
  },
);

it("keeps Resume aria-disabled while pending and after success until the state changes", async () => {
  let finish!: () => void;
  onResume.mockReturnValue(
    new Promise<void>((resolve) => {
      finish = resolve;
    }),
  );
  const pending = render("paused").click();
  expect(render("paused").html).toContain('aria-disabled="true"');
  await render("paused").click();
  expect(onResume).toHaveBeenCalledExactlyOnceWith("agent-1");
  finish();
  await pending;
  expect(render("paused").html).toContain('aria-disabled="true"');
  expect(render("paused").html).toContain("Resume</button>");
  await render("paused").click();
  expect(onResume).toHaveBeenCalledExactlyOnceWith("agent-1");
  expect(render("working").html).toContain('aria-disabled="false"');
  expect(render("paused").html).toContain('aria-disabled="false"');
});

it("ignores a late pause response after the status changes", async () => {
  let finish!: () => void;
  onPause.mockReturnValue(
    new Promise<void>((resolve) => {
      finish = resolve;
    }),
  );
  const pending = render().click();
  render("paused");
  finish();
  await pending;
  expect(render("paused").html).toContain("Resume</button>");
  expect(render("paused").html).toContain('aria-disabled="false"');
});
