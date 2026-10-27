import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { twoSlotState } from "../taskBox.fixture";

import { createTaskBoxStore } from "./taskBox";
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
    "keeps slot names stable and marks only selected slot %s with a visible, decorative check and aria-pressed",
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
          slot === "A" ? "A Muse Spark" : "B Muse Spark Open",
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

  it("shows the two offered letters and model labels, with B's token, and disables a named holder", () => {
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
    expect(slots[0]).toContain("text-agent-a-text");
    expect(slots[1]).toContain('aria-disabled="false"');
    expect(slots[1]).toContain('aria-pressed="true"');
    expect(slots[1]).toContain("text-agent-b-text");
    for (const info of run.slots) {
      expect(markup).toContain(run.models.find((model) => model.id === info.default_model)?.label);
    }
    const one = { ...state, run: { ...run, slots: run.slots.slice(0, 1) } };
    expect(renderToStaticMarkup(<TaskBox state={one} onStart={vi.fn()} />)).not.toContain(
      'aria-label="Agent slot"',
    );
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
    expect(markup).toContain(">Enter</kbd>");
    expect(markup).toMatch(/>(Ctrl|Cmd)<\/kbd>/);
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
