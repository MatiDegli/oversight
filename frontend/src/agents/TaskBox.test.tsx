import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { CommandError, createCommandClient } from "../transport";
import { twoSlotState } from "../taskBox.fixture";

import { createTaskBoxStore } from "./taskBoxModel";
import { TaskBox, TaskBoxView } from "./TaskBox";
import type { TaskBoxViewProps } from "./TaskBox";

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());

const view = (props: Partial<TaskBoxViewProps> = {}) =>
  renderToStaticMarkup(
    <TaskBoxView
      draft=""
      pending={false}
      failure={null}
      block={null}
      onDraft={vi.fn()}
      onStart={vi.fn()}
      {...props}
    />,
  );
const button = (markup: string) => /<button [^>]*>/.exec(markup)?.[0] ?? "";

describe("the new-agent box", () => {
  it("preselects A when both slots are free in the browser's normal two-slot setup", () => {
    const markup = renderToStaticMarkup(
      <TaskBox state={twoSlotState()} onStart={vi.fn()} store={createTaskBoxStore()} />,
    );
    const slots = [...markup.matchAll(/<button [^>]*aria-pressed[^>]*>.*?<\/button>/g)].map(
      (match) => match[0],
    );
    expect(slots).toHaveLength(2);
    expect(slots[0]).toContain('aria-disabled="false"');
    expect(slots[0]).toContain('aria-pressed="true"');
    expect(slots[0]).toContain("aria-pressed:border-text");
    expect(slots[0]).toMatch(/<svg[^>]*aria-hidden="true"/);
    expect(slots[1]).toContain('aria-disabled="false"');
    expect(slots[1]).toContain('aria-pressed="false"');
  });

  it.each(["A", "B", null] as const)(
    "shows model names only and marks only selected slot %s with a visible, decorative check and aria-pressed",
    (selected) => {
      const markup = view({
        slots: [
          { slot: "A", label: "Muse Spark", holder: null },
          { slot: "B", label: "Muse Spark Open", holder: null },
        ],
        selected,
      });
      const buttons = [...markup.matchAll(/<button [^>]*aria-pressed[^>]*>.*?<\/button>/g)];
      expect(buttons).toHaveLength(2);
      for (const [index, slot] of ["A", "B"].entries()) {
        const button = buttons[index]?.[0] ?? "";
        expect(button).toContain(`aria-pressed="${String(selected === slot)}"`);
        expect(button).not.toMatch(/aria-label=|aria-labelledby=|<title/);
        expect(button.replace(/<[^>]*>/g, "").trim()).toBe(
          slot === "A" ? "Muse Spark" : "Muse Spark Open",
        );
        if (selected === slot) {
          expect(button).toContain("aria-pressed:border-text");
          expect(button).toContain("lucide-check");
          const check = /<svg[^>]*>/.exec(button)?.[0] ?? "";
          expect(check).toContain('aria-hidden="true"');
          expect(check).not.toContain('role="img"');
          expect(check).toContain("inline-block size-4 text-text");
          expect(check).not.toMatch(/class="[^"]*\b(hidden|invisible|sr-only)\b|style=/);
        } else {
          expect(button).not.toContain("aria-pressed:border-text");
          expect(button).not.toContain("lucide-check");
          expect(button).not.toContain('aria-label="Selected"');
        }
      }
    },
  );

  it("shows only the two offered model labels and disables a named holder", () => {
    const state = through(14);
    const run = state.run;
    if (!run) throw new Error("Missing run fixture");
    const markup = renderToStaticMarkup(
      <TaskBox state={state} onStart={vi.fn()} store={createTaskBoxStore()} />,
    );
    expect(markup).toContain('aria-label="Agent slot"');
    const slots = [...markup.matchAll(/<button [^>]*aria-pressed[^>]*>.*?<\/button>/g)].map(
      (match) => match[0],
    );
    expect(slots).toHaveLength(2);
    expect(slots[0]).toContain('aria-disabled="true"');
    expect(slots[0]).toContain("Agent A is working.");
    expect(slots[0]).not.toContain("--slot-");
    expect(slots[1]).toContain('aria-disabled="false"');
    expect(slots[1]).toContain('aria-pressed="true"');
    expect(slots[1]).not.toContain("text-agent-b-text");
    for (const info of run.slots) {
      expect(markup).toContain(run.models.find((model) => model.id === info.default_model)?.label);
    }
    const one = { ...state, run: { ...run, slots: run.slots.slice(0, 1) } };
    expect(renderToStaticMarkup(<TaskBox state={one} onStart={vi.fn()} />)).not.toContain(
      'aria-label="Agent slot"',
    );
  });
  it("shows one chip when both slots offer the same profile", () => {
    const state = twoSlotState();
    if (!state.run) throw new Error("Missing run");
    state.run = {
      ...state.run,
      slots: state.run.slots.map((slot) => ({ ...slot, default_model: "muse-spark" })),
    };
    const markup = renderToStaticMarkup(
      <TaskBox state={state} onStart={vi.fn()} store={createTaskBoxStore()} />,
    );
    expect(markup).toContain('aria-label="Model: Muse Spark"');
    expect(markup).not.toContain('aria-label="Agent slot"');
  });

  it("draws the field once, around the text and its chips, and names the box for screen readers", () => {
    const markup = view({ draft: "x" });
    const box = /<div class="[^"]*bg-field[^"]*">/.exec(markup)?.[0] ?? "";
    expect(box).toContain("border-control-edge");
    expect(box).toContain("rounded-card");
    expect(box).toContain("has-[textarea:focus-visible]:outline-text");
    // The textarea has no border or fill of its own and still shows nothing without a ring on its box.
    const textarea = /<textarea [^>]*>/.exec(markup)?.[0] ?? "";
    expect(textarea).not.toMatch(/class="[^"]*\b(border|bg-field)\b/);
    expect(textarea).toContain("placeholder:text-faint");
    expect(textarea).toContain("Each agent works in its own worktree");
    expect(markup).toMatch(/<h3 [^>]*class="[^"]*sr-only[^"]*">New agent<\/h3>/);
    expect(markup.indexOf("<h3")).toBeLessThan(markup.indexOf("<textarea"));
  });

  it("shows the model and the step limit as chips before Start agent, which is not a chip", () => {
    const markup = view({ draft: "x", model: "Muse Spark", maxSteps: 30 });
    const chips = [...markup.matchAll(/<span [^>]*bg-page-raised[^>]*>.*?<\/span>/g)].map(
      (match) => match[0],
    );
    expect(chips).toHaveLength(2);
    expect(chips[0]).toContain("Muse Spark");
    expect(chips[0]).toMatch(/<svg[^>]*aria-hidden="true"/);
    expect(chips[0]).toContain('aria-label="Model: Muse Spark"');
    expect(chips[1]).toContain("Up to 30 steps");
    for (const chip of chips) {
      expect(chip).toContain("border-page-border");
      expect(chip).toContain("h-(--chip-height)");
      expect(chip).toContain("rounded-control");
    }
    expect(markup.indexOf("Up to 30 steps")).toBeLessThan(markup.indexOf(">Start agent<"));
    expect(markup).not.toMatch(/<button[^>]*>[^<]*(Muse Spark|Up to 30 steps)/);
    expect(button(markup)).toContain("ml-auto");
  });

  it("makes the model chip a menu button and the step chip a way to Settings when both can act", () => {
    const markup = view({
      draft: "x",
      model: "Muse Spark",
      models: [{ id: "muse-spark", label: "Muse Spark" }],
      modelId: "muse-spark",
      onModel: vi.fn(),
      maxSteps: 30,
      onSettings: vi.fn(),
    });
    const chip = /<button [^>]*aria-label="Model: Muse Spark"[^>]*>/.exec(markup)?.[0] ?? "";
    expect(chip).toContain('aria-haspopup="menu"');
    expect(chip).toContain('aria-expanded="false"');
    expect(chip).toContain('aria-disabled="false"');
    expect(chip).toContain("border-page-border");
    // Closed until the person opens it; nothing takes focus on its own.
    expect(markup).not.toContain('role="menu"');
    expect(markup).toMatch(
      /<button [^>]*aria-label="Up to 30 steps. Change the step limit in Settings"[^>]*>Up to 30 steps<\/button>/,
    );
    expect(markup.indexOf("Up to 30 steps")).toBeLessThan(markup.indexOf(">Start agent<"));
    // A pending start keeps the model it was sent with.
    expect(
      view({
        draft: "x",
        pending: true,
        model: "Muse Spark",
        models: [{ id: "muse-spark", label: "Muse Spark" }],
        onModel: vi.fn(),
      }),
    ).toMatch(
      /<button [^>]*aria-disabled="true"[^>]*aria-label="Model: Muse Spark"|aria-label="Model: Muse Spark"[^>]*aria-disabled="true"/,
    );
    // Without Settings the step limit stays a plain fact.
    expect(view({ draft: "x", maxSteps: 30 })).toMatch(/<span [^>]*>Up to 30 steps<\/span>/);
  });

  it("offers example tasks under an empty, idle box, as buttons that only fill it", () => {
    const examples = [
      { label: "Reject negative prices", task: "compute_price in shop/pricing.py" },
    ];
    const markup = view({ examples });
    expect(markup).toContain('role="group" aria-label="Example tasks"');
    expect(markup).toContain(">Try:<");
    expect(markup).toMatch(
      /<button type="button" title="compute_price in shop\/pricing.py"[^>]*>Reject negative prices<\/button>/,
    );
    expect(markup.indexOf("Example tasks")).toBeGreaterThan(markup.indexOf("<textarea"));
    // Text in the box, or a start on its way, hides them.
    expect(view({ examples, draft: "x" })).not.toContain("Example tasks");
    expect(view({ examples, draft: "x", pending: true })).not.toContain("Example tasks");
    expect(view()).not.toContain("Example tasks");
  });

  it("shows the demo shop's examples only before any agent, and none in a replay or elsewhere", () => {
    const render = (state: ReturnType<typeof through>, onStart?: () => Promise<unknown>) =>
      renderToStaticMarkup(
        <TaskBox state={state} onStart={onStart} store={createTaskBoxStore()} />,
      );
    const opening = through(2);
    const first = render(opening, vi.fn());
    expect(first).toContain(">Reject negative prices<");
    expect(first).toContain(">Validate reserve quantities<");
    expect(first).toContain(">Leave cancelled orders out of revenue<");
    expect(render(through(4), vi.fn())).not.toContain("Example tasks");
    expect(render(opening)).not.toContain("Example tasks");
    const run = opening.run;
    if (!run) throw new Error("Missing run fixture");
    expect(render({ ...opening, run: { ...run, workspace: "my-app" } }, vi.fn())).not.toContain(
      "Example tasks",
    );
  });

  it("leaves out a chip it has no fact for", () => {
    const markup = view({ draft: "x" });
    expect(markup).not.toContain("bg-page-raised");
    expect(markup).not.toContain("Up to");
    expect(view({ draft: "x", maxSteps: 1 })).toContain("Up to 1 step<");
  });

  it("takes the chips' facts from the run: the only model's label, and the step limit", () => {
    const state = through(2);
    const run = state.run;
    if (!run) throw new Error("Missing run fixture");
    const one = { ...state, run: { ...run, slots: run.slots.slice(0, 1) } };
    const oneMarkup = renderToStaticMarkup(
      <TaskBox state={one} onStart={vi.fn()} store={createTaskBoxStore()} />,
    );
    expect(oneMarkup).toContain('aria-label="Model: Muse Spark"');
    expect(oneMarkup).toContain("Up to 30 steps");
    // With two slots the picker's own buttons name each model, so there is no third chip for it.
    const twoMarkup = renderToStaticMarkup(
      <TaskBox state={state} onStart={vi.fn()} store={createTaskBoxStore()} />,
    );
    expect(twoMarkup).not.toContain("Model:");
    expect(twoMarkup).toContain("Up to 30 steps");
    // Before the server's run.started there is nothing to say.
    const waiting = renderToStaticMarkup(
      <TaskBox state={createInitialState()} onStart={vi.fn()} store={createTaskBoxStore()} />,
    );
    expect(waiting).not.toContain("Model:");
    expect(waiting).not.toContain("Up to");
  });

  it("is disabled while the task is blank or only whitespace, and enabled with a task", () => {
    for (const draft of ["", "   ", "\n\t\n"])
      expect(button(view({ draft }))).toContain('aria-disabled="true"');
    expect(button(view({ draft: "Cap the discount" }))).toContain('aria-disabled="false"');
  });

  it("is a primary Start agent button under a textarea, with the keyboard hint", () => {
    const markup = view({ draft: "x" });
    expect(markup).toContain("<textarea");
    expect(markup).toContain(">Start agent</button>");
    expect(button(markup)).toContain("bg-text");
    expect(button(markup)).toContain("text-page");
    expect(markup).toContain(">Enter</kbd> to start");
    expect(markup).toContain(">Shift</kbd>+<kbd");
    expect(markup).toContain("for a new line");
    // Without a queue, no key promises one.
    expect(markup).not.toContain("to queue");
  });

  it("offers Ctrl+Enter to queue, and says so when no slot is free", () => {
    const markup = view({ draft: "x", onQueue: vi.fn(), block: "Agent A is working." });
    expect(markup).toMatch(/>(Ctrl|Cmd)<\/kbd>\+<kbd[^>]*>Enter<\/kbd> to queue/);
    expect(markup).toMatch(/Agent A is working\. (Ctrl|Cmd)\+Enter queues the task\./);
  });

  it("lists the person's queued tasks with their state and a cancel while they wait", () => {
    const item = (id: string, state: "queued" | "starting" | "blocked", detail: string | null) => ({
      item: {
        queued_task_id: id,
        revision: 1,
        state,
        agent_ids: [],
        detail,
        agent: { task: `Task ${id}`, slot: null, model: null, zone: null },
        plan_id: null,
        plan_revision: null,
        started_by: null,
      },
      onCancel: vi.fn(() => Promise.resolve()),
    });
    const markup = view({
      notice: "Queued. It starts when a slot is free.",
      queued: [
        item("q1", "queued", null),
        item("q2", "blocked", "spend brake"),
        item("q3", "starting", null),
      ],
    });
    expect(markup).toContain("Queued (3)");
    expect(markup).toContain("Queued. It starts when a slot is free.");
    expect(markup).toContain("Waiting for a slot");
    expect(markup).toContain("Blocked: spend brake");
    expect(markup).toContain('aria-label="Cancel queued task: Task q1"');
    expect(markup).toContain('aria-label="Cancel queued task: Task q2"');
    // A task already starting can no longer be cancelled from the queue.
    expect(markup).not.toContain('aria-label="Cancel queued task: Task q3"');
  });

  it("is disabled, with its reason, while no slot is free", () => {
    const markup = view({ draft: "Cap the discount", block: "Agent A is working." });
    expect(button(markup)).toContain('aria-disabled="true"');
    expect(markup).toContain("Agent A is working.");
    // The reason sits in the live region that the button points to.
    const id = /aria-describedby="([^"]+)"/.exec(button(markup))?.[1] ?? "missing";
    expect(markup).toContain(`<div role="status" id="${id}"`);
  });

  it("is disabled and read-only while a start is pending, and says so", () => {
    const markup = view({ draft: "Cap the discount", pending: true });
    expect(button(markup)).toContain('aria-disabled="true"');
    expect(/<textarea [^>]*>/.exec(markup)?.[0]).toContain('readOnly=""');
    expect(markup).toContain("Starting the agent.");
  });

  it("never uses the disabled attribute, which would drop the person's focus", () => {
    for (const props of [{}, { draft: "x" }, { draft: "x", pending: true }, { block: "Busy." }])
      expect(view(props)).not.toMatch(/ disabled(=|>| )/);
  });

  it("shows a failure under the box as an alert with an icon, never in color alone", () => {
    const markup = view({ draft: "Cap the discount", failure: "Slot A is busy." });
    expect(markup).toMatch(/<p role="alert"[^>]*text-text[^>]*><svg[^>]*aria-hidden="true"/);
    expect(markup).not.toContain("text-failed");
    expect(markup).toContain("Slot A is busy.");
    expect(button(markup)).toContain('aria-disabled="false"');
    expect(view({ draft: "x" })).not.toContain('role="alert"');
  });

  it("takes every color from a theme token", () => {
    expect(view({ draft: "x", failure: "Oops." })).not.toMatch(/#[0-9a-f]{3,8}\b|rgb|hsl|\[#/i);
  });
});

describe("the new-agent box in a replay", () => {
  const box = (replayOf?: string) =>
    renderToStaticMarkup(<TaskBox state={createInitialState()} replayOf={replayOf} />);

  it("names a hand-written fixture as one, never as a recording, and renders no live control", () => {
    const markup = box("hand-written demo fixture");
    expect(markup).toContain("New agent");
    expect(markup).toContain(
      "Replay of a hand-written demo fixture: agents cannot be started here.",
    );
    expect(markup).not.toMatch(/recorded run/i);
    for (const live of ["<textarea", "<button", "Start agent", "<kbd"])
      expect(markup).not.toContain(live);
  });

  it("calls a log a real recorded run only when its source says so", () => {
    expect(box("real recorded run")).toContain(
      "Replay of a real recorded run: agents cannot be started here.",
    );
    expect(box()).toContain("Replay: agents cannot be started here.");
    expect(box()).not.toMatch(/recorded run/i);
  });
});

it("offers the existing Settings opener only after a not_configured refusal", async () => {
  const store = createTaskBoxStore();
  store.getState().setDraft("Keep this task");
  const client = createCommandClient(
    vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            error: {
              code: "not_configured",
              message: "Server setup is incomplete.",
              missing: [
                { name: "OVERSIGHT_BUDGET_USD", problem: "missing", slots: ["A"] },
                { name: "MUSE_SPARK_INPUT_USD_PER_M", problem: "missing", slots: ["A"] },
                { name: "MUSE_SPARK_OUTPUT_USD_PER_M", problem: "missing", slots: ["A"] },
              ],
            },
          }),
          { status: 503 },
        ),
      ),
    ),
  );
  await store.getState().start(client.startAgent);
  const render = () =>
    renderToStaticMarkup(
      <TaskBox
        state={twoSlotState()}
        store={{ ...store, getInitialState: store.getState }}
        onStart={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );
  expect(render()).toContain("Open settings");
  expect(render()).toContain(
    "Setup is incomplete: Meta budget (USD), Muse Spark input (USD / 1M tokens), and Muse Spark output (USD / 1M tokens) are missing.",
  );
  expect(render()).not.toContain("The server failed to start");
  expect(render()).toContain("Keep this task");
  store.getState().setDraft("Edited task");
  expect(render()).not.toContain("Open settings");
  await store.getState().start(() => Promise.reject(new CommandError(409, "slot_busy", "Busy.")));
  expect(render()).not.toContain("Open settings");
});
