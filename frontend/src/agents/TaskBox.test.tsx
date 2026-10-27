import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { createInitialState } from "../state";
import { TaskBox, TaskBoxView } from "./TaskBox";
import type { TaskBoxViewProps } from "./TaskBox";

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
    expect(markup).toMatch(/<p role="alert"[^>]*text-failed-text[^>]*><svg[^>]*aria-hidden="true"/);
    expect(markup).toContain("Slot A is busy.");
    expect(button(markup)).toContain('aria-disabled="false"');
    expect(view({ draft: "x" })).not.toContain('role="alert"');
  });

  it("takes every color from a theme token", () => {
    expect(view({ draft: "x", failure: "Oops." })).not.toMatch(/#[0-9a-f]{3,8}\b|rgb|hsl|\[#/i);
  });
});

describe("the new-agent box in a recorded run", () => {
  it("says so and renders no live control", () => {
    const markup = renderToStaticMarkup(<TaskBox state={createInitialState()} />);
    expect(markup).toContain("New agent");
    expect(markup).toContain("Recorded run: agents cannot be started here.");
    for (const live of ["<textarea", "<button", "Start agent", "<kbd"])
      expect(markup).not.toContain(live);
  });
});
