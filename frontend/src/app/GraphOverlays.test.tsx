import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { createInitialState } from "../state";
import { DetailControls, GraphHeading, Legend, ZoomControls } from "./GraphOverlays";
import { through } from "./replay.fixture";

describe("the graph heading", () => {
  it("names the repository, what is drawn of it and how many agents are at work", () => {
    const watching = renderToStaticMarkup(<GraphHeading state={through(4)} />);
    expect(watching).toContain(">demo-repo<");
    expect(watching).toContain(">6 modules<");
    expect(watching).toContain(">36 functions<");
    expect(watching).toContain(">1 agent working<");
    // Ready for review is not work in progress.
    expect(renderToStaticMarkup(<GraphHeading state={through(20)} />)).toContain(
      ">0 agents working<",
    );
  });

  it("says nothing before a run has started", () => {
    expect(renderToStaticMarkup(<GraphHeading state={createInitialState()} />)).toBe("");
  });
});

describe("the legend", () => {
  it("lists an agent for each slot of the run and the looks the graph draws, each in words", () => {
    const markup = renderToStaticMarkup(<Legend state={through(4)} />);
    const words = [
      "Agent A",
      "Agent B",
      "Read",
      "Editing or changed",
      "Not in main yet",
      "Possible call",
    ];
    for (const word of words) expect(markup).toContain(word);
    // The sketch's order: the agents, then reading, editing, what main lacks, and a possible call.
    const places = words.map((word) => markup.indexOf(word));
    expect(places).toEqual([...places].sort((x, y) => x - y));
    // Glyphs carry no meaning to a reader of the page: the words do.
    expect(markup.match(/aria-hidden="true"/g)).toHaveLength(6);
  });

  it("draws editing or changed as a dashed, filled box and not-in-main as a dashed outline", () => {
    const markup = renderToStaticMarkup(<Legend state={through(4)} />);
    const glyph = (words: string) =>
      new RegExp(`<span aria-hidden="true" class="([^"]*)"></span>${words}`).exec(markup)?.[1] ??
      "";
    expect(glyph("Editing or changed")).toContain("border-dashed");
    expect(glyph("Editing or changed")).toContain("bg-raised");
    expect(glyph("Not in main yet")).toContain("border-dashed");
    expect(glyph("Not in main yet")).not.toContain("bg-raised");
    // Read is the only solid one: a dashed border means a pending change and nothing else.
    expect(glyph("Read")).not.toContain("border-dashed");
    expect(glyph("Possible call")).not.toContain("border-dashed");
  });

  it("is absent before a run has started", () => {
    expect(renderToStaticMarkup(<Legend state={createInitialState()} />)).toBe("");
  });
});

/** The buttons of the controls' element tree, in order, so a test can press them without a DOM. */
function pressAll(api: Parameters<typeof ZoomControls>[0]["api"]) {
  const group = ZoomControls({ api }) as ReactElement<{
    children: ReactElement<{ onClick: () => void }>[];
  }>;
  for (const button of group.props.children) button.props.onClick();
}

describe("the zoom controls", () => {
  it("press through to zoom in, zoom out and fit, in that order", () => {
    const calls: string[] = [];
    pressAll({
      current: {
        zoomIn: () => calls.push("in"),
        zoomOut: () => calls.push("out"),
        fit: () => calls.push("fit"),
        reveal: () => calls.push("reveal"),
      },
    });
    expect(calls).toEqual(["in", "out", "fit"]);
    expect(() => {
      pressAll({ current: null });
    }).not.toThrow();
  });

  it("are three named buttons that ask the graph, and do nothing while it does not exist", () => {
    const api = { zoomIn: vi.fn(), zoomOut: vi.fn(), fit: vi.fn(), reveal: vi.fn() };
    const markup = renderToStaticMarkup(<ZoomControls api={{ current: api }} />);
    for (const label of ["Zoom in", "Zoom out", "Fit the graph to the view"])
      expect(markup).toContain(`aria-label="${label}"`);
    expect(markup.match(/<button /g)).toHaveLength(3);
    // The buttons call through the ref at click time, so a graph created later still answers.
    expect(() => renderToStaticMarkup(<ZoomControls api={{ current: null }} />)).not.toThrow();
  });
});

it("uses the approved map count, readout and legend copy", () => {
  expect(renderToStaticMarkup(<GraphHeading state={through(4)} groups={2} />)).toContain(
    ">2 groups<",
  );
  const legend = renderToStaticMarkup(<Legend state={through(4)} map />);
  expect(legend).toContain("A module, with its function count");
  expect(legend).toContain("A ring: an agent works inside");
  expect(legend).not.toContain("Possible call");
  expect(legend).not.toContain("Agent A");
  const readout = renderToStaticMarkup(
    <DetailControls level="Modules" zoom={64} map onChange={() => undefined} />,
  );
  expect(readout).toContain('aria-label="Zoom percentage"');
  expect(readout).toContain("64%");
  expect(readout).toContain(" · Map");
  expect(readout).toContain("font-semibold text-text");
});
